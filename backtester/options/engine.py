#!/usr/bin/env python3
"""
backtester.options.engine — daily replay engine for `tasty_credit_spreads` (Phase 9, D-02/D-03/D-11..13).

Feeds day-t rows through the UNMODIFIED `bot/options/strategy.py` functions
(import, never copy — OBT-01/D-02), applies the live portfolio caps in the
live gate order (D-13, `bot/options/service.py:467-541`), fills each leg at
mid +/- slippage (D-11), manages/settles positions (D-12), and appends one
D-16-shaped row per closed trade to `self.trade_log`.

Documented divergences from live (Claude's Discretion / RESEARCH Pitfall 3):
  1. Decision point is day t's CLOSE: option mids, underlying close and
     change_pct are all as-of the same instant. This is what makes D-08's
     no-look-ahead rule coherent at daily resolution — there is no separate
     "premarket scan" vs "intraday manage" moment offline.
  2. Underlyings are iterated `sorted(symbols)` (deterministic, ticker-string
     order). Live iterates `sorted(_group_rows_by_underlying(rows).items())`,
     which sorts by an opaque broker `stock_id` integer with no offline
     analog. Iteration order decides who gets capacity when
     `max_concurrent_positions`/`max_new_positions_per_day` binds, so it must
     be picked and documented, not left accidental (RESEARCH Pitfall 3).
  3. Bid/ask are synthesized from the close (`synthesize_bid_ask`) — Massive's
     `O:` daily aggregates carry no bid/ask (D-11) — so `leg_is_liquid`'s
     spread gate is never the binding constraint offline; only the OI gate is.
  4. D-08's no-look-ahead rule applies to ENTRY selection only (exact-day bar
     required, `OptionChainSource.rows_for`). Marking an OPEN position on a
     day with no bar for one of its legs uses `last_known_close`
     (carry-forward) — the Claude's Discretion "missing-bar handling for a
     leg on a manage day" call, chosen and documented here. Entry selection
     never uses this fallback.

T-09-13/T-09-14 (VERIFICATION gap closure): `update_iv`/`_entry_scan` fetch
bars LAZILY through `chain.rows_for` (one expiry, an OTM-side band) instead
of the old eager `contracts_for_day`. `close_open_at_end` settles every
position still open at the replay window's end (CR-01 — never silently
dropped from reported metrics). The daily-loss breaker (`_check_daily_
breaker`) evaluates realized + unrealized P&L, mirroring live's two trip
points (WR-02).

Never imports the live gateway module, the options execution/order layer, the
options service process, or the broker SDK package — this module is provably
broker-free (T-09-E1; see test_no_broker_imports).

Exports: synthesize_bid_ask, leg_fill_price, settle_at_expiry, build_rows,
         OptionsBacktestEngine
"""
from datetime import date

# Import-not-copy (D-02, OBT-01): every strategy decision comes from these
# nine functions by direct import — a copy-paste re-implementation fails
# test_imports_not_copies's `is` identity assertion.
from bot.options.strategy import (
    is_monthly_expiry,
    leg_is_liquid,
    manage_decision,
    mark_spread,
    option_dte,
    passes_entry_gate,
    pick_expiry,
    pick_strikes,
    size_position,
)

from backtester.options.greeks import atm_iv, bs_delta, dte_to_years, implied_vol, iv_rank

_CONTRACT_MULTIPLIER = 100


# ============================================================
# Fill model + expiry settlement (D-11/D-12)
# ============================================================

def synthesize_bid_ask(close: float, spread_pct: float) -> tuple:
    """D-11: no bid/ask in Massive aggs — synthesize close +/- spread_pct/2 so
    `leg_is_liquid()` can run unmodified. Mid == close exactly (symmetric)."""
    half = close * spread_pct / 200.0
    return max(close - half, 0.01), close + half


def leg_fill_price(mid: float, side: str, slippage_usd: float) -> float:
    """Adverse slippage: paying more to BUY, receiving less to SELL (matches
    `backtester/execution.py`'s SimulatedExecution adverse-slippage convention)."""
    return mid + slippage_usd if side == "BUY" else mid - slippage_usd


def settle_at_expiry(legs: list, underlying_close: float) -> float:
    """Intrinsic-value settlement (D-12). legs: dicts with "right"/"strike"/"side".

    Returns the net cost to close, same SELL-minus-BUY sign convention as
    `mark_spread` — a fully OTM spread settles for 0 (max profit = credit
    retained); an ITM short settles at its intrinsic difference.
    """
    total = 0.0
    for leg in legs:
        intrinsic = (max(underlying_close - leg["strike"], 0.0) if leg["right"] == "C"
                     else max(leg["strike"] - underlying_close, 0.0))
        total += intrinsic if leg["side"] == "SELL" else -intrinsic
    return total


# ============================================================
# Row-dict shape parity with the live gateway (Pattern 2)
# ============================================================

def build_rows(chain_rows, underlying_px, day, r, spread_pct, oi_source, min_open_interest):
    """Turn `OptionChainSource.contracts_for_day()` rows into live-shaped rows.

    Output keys are EXACTLY `code`, `right`, `strike`, `delta`, `bid`, `ask`,
    `open_interest`, `expiry` (isoformat string), `dte` (int) — fed straight
    into `pick_strikes`/`leg_is_liquid` with no adaptation — plus `bar_volume`
    (carried for `min_leg_volume` reporting; the strategy ignores unknown
    keys). A contract whose `implied_vol` fails to solve (below the
    no-arbitrage floor, degenerate DTE) is DROPPED, never emitted with a NaN
    delta (RESEARCH Pitfall 5).
    """
    today = date.fromisoformat(day) if isinstance(day, str) else day
    rows = []
    for c in chain_rows:
        t_years = dte_to_years(c["expiry"], today)
        sigma = implied_vol(c["close"], underlying_px, c["strike"], t_years, r, c["right"])
        if sigma is None:
            continue
        delta = bs_delta(underlying_px, c["strike"], t_years, r, sigma, c["right"])
        bid, ask = synthesize_bid_ask(c["close"], spread_pct)
        open_interest = c["volume"] if oi_source == "volume" else min_open_interest
        rows.append({
            "code": c["ticker"],
            "right": c["right"],
            "strike": c["strike"],
            "delta": delta,
            "bid": bid,
            "ask": ask,
            "open_interest": open_interest,
            "expiry": c["expiry"].isoformat(),
            "dte": c["dte"],
            "bar_volume": c["volume"],
        })
    return rows


# ============================================================
# Daily replay engine
# ============================================================

class OptionsBacktestEngine:
    """Daily replay controller (D-03: one decision point per trading day).

    cfg:    OptionsConfig (bot.options.config.load_options_config).
    chains: {underlying_code: OptionChainSource} — one per universe symbol.
    """

    def __init__(self, cfg, chains: dict, r: float = 0.045, slippage_usd: float = 0.02,
                 commission_per_leg: float = 0.65, spread_pct: float = 2.0,
                 oi_source: str = "volume", strike_band_pct: float = 10.0):
        self.cfg = cfg
        self.chains = chains
        self.r = r
        self.slippage_usd = slippage_usd
        self.commission_per_leg = commission_per_leg
        self.spread_pct = spread_pct
        self.oi_source = oi_source
        self.strike_band_pct = strike_band_pct

        self.trade_log: list = []
        self._open: dict = {}            # code -> position dict
        self._iv_series: dict = {}       # code -> [daily ATM IV, oldest first]
        self._opened_on: dict = {}       # code -> "YYYY-MM-DD" of the open position
        self._breaker_days: set = set()  # "YYYY-MM-DD" days the breaker has tripped
        self._prior_close: dict = {}     # code -> yesterday's underlying close
        self._unrealized_today: float = 0.0  # WR-02: populated by _manage_day
        self.open_positions_at_end: int = 0  # CR-01: populated by close_open_at_end

    # --------------------------------------------------------
    # Replay loop
    # --------------------------------------------------------

    def run(self, days: list) -> None:
        """Replay every trading day in `days`, in order."""
        for day in days:
            self.run_day(day)

    def update_iv(self, day: str) -> None:
        """Update each chain's daily ATM-IV observation (T-09-13 lazy fetch,
        VERIFICATION gap 3): picks the IV-TRACKING expiry (closest to
        cfg.target_dte within [min_dte, max_dte], ties -> earlier date) from
        the reference via `expiries_for_day`, then fetches only a tight
        +/-1% ATM band for that one expiry via `rows_for` — never the whole
        chain. The single source of truth for the IV-update half of a
        decision day: called from `run_day` (below) AND from
        `options_run._prime_iv_series`'s warm-up loop, so there is no
        duplicated copy of this logic (REVIEW.md IN-07-adjacent concern).
        """
        cfg = self.cfg
        for code, chain in sorted(self.chains.items()):
            underlying_px = chain.underlying_close(day)
            if underlying_px is None:
                continue
            candidates = [
                (exp, dte) for exp, dte in chain.expiries_for_day(day)
                if cfg.min_dte <= dte <= cfg.max_dte
            ]
            if not candidates:
                continue
            exp, _ = min(candidates, key=lambda e: (abs(e[1] - cfg.target_dte), e[0]))
            rows = chain.rows_for(day, exp, underlying_px, band_pct=1.0)
            iv = atm_iv(rows, underlying_px, day, self.r, cfg.target_dte)
            if iv is not None:
                self._iv_series.setdefault(code, []).append(iv)

    def run_day(self, day: str) -> None:
        """One decision point (D-03): update IV series, manage/settle, then enter.

        Manage/settlement runs BEFORE the entry scan (a position closed today
        frees capacity today's entry scan can use, mirroring the live
        schedule where `options_manage` fires every few minutes and
        `options_entry_scan` fires once or twice — manage has already run by
        the time any given entry scan does).
        """
        today = date.fromisoformat(day)
        change_pct = {}

        for code, chain in sorted(self.chains.items()):
            underlying_px = chain.underlying_close(day)
            if underlying_px is None:
                continue
            prior = self._prior_close.get(code)
            change_pct[code] = ((underlying_px - prior) / prior * 100.0) if prior else None
            self._prior_close[code] = underlying_px

        self.update_iv(day)
        self._manage_day(day)
        self._entry_scan(day, today, change_pct)

    # --------------------------------------------------------
    # Manage + settlement (D-12)
    # --------------------------------------------------------

    def _manage_day(self, day: str) -> None:
        """Mark every open position from that day's synthesized quotes, settle
        expired positions at intrinsic, close positions the imported
        `manage_decision` says to close. A leg with no bar on `day` falls back
        to `last_known_close` for MARKING only (never entry selection) and the
        trade-log row records that a carried mark was used.

        Also accumulates `self._unrealized_today` — the mark-to-market P&L of
        every position that stays open today — mirroring
        `bot/options/service.py`'s `_manage_position` return value /
        `_job_manage`'s `unrealized_total` sum (service.py:705-718), so the
        daily-loss breaker sees unrealized P&L exactly as live does (WR-02).
        """
        cfg = self.cfg
        today = date.fromisoformat(day)
        unrealized = 0.0

        for code, pos in list(self._open.items()):
            chain = self.chains[code]
            expiry = date.fromisoformat(pos["expiry"])

            if today >= expiry:
                underlying_close = chain.underlying_close(day)
                if underlying_close is None:
                    continue  # no bar today to settle against; retry next day
                net_exit = settle_at_expiry(pos["legs"], underlying_close)
                self._record_close(code, pos, day, "expired", net_exit,
                                   close_commission=0.0, carried_mark=False)
                continue

            quotes = {}
            carried = False
            missing = False
            for leg in pos["legs"]:
                close = chain.bar_close(leg["code"], day)
                if close is None:
                    close = chain.last_known_close(leg["code"], day)
                    carried = True
                if close is None:
                    missing = True
                    break
                bid, ask = synthesize_bid_ask(close, self.spread_pct)
                quotes[leg["code"]] = {"bid": bid, "ask": ask}
            if missing:
                continue  # cannot mark this position today; try again next day

            dte = option_dte(expiry, today)
            mark = mark_spread(pos["legs"], quotes)
            credit = float(pos["credit_per_spread"])
            reason = manage_decision(mark, credit, dte, cfg)
            if reason is None:
                # Still open -- accumulate its unrealized P&L for the breaker.
                unrealized += (credit - mark) * _CONTRACT_MULTIPLIER * pos["qty"]
                continue

            net_exit = 0.0
            for leg in pos["legs"]:
                q = quotes[leg["code"]]
                mid = (q["bid"] + q["ask"]) / 2
                close_side = "BUY" if leg["side"] == "SELL" else "SELL"
                fill = leg_fill_price(mid, close_side, self.slippage_usd)
                net_exit += fill if leg["side"] == "SELL" else -fill

            close_commission = self.commission_per_leg * len(pos["legs"]) * pos["qty"]
            self._record_close(code, pos, day, reason, net_exit, close_commission, carried)

        self._unrealized_today = unrealized

    def close_open_at_end(self, day: str) -> None:
        """Mark-to-market every position still in `self._open` on `day` (the
        last replay day) and record it with `exit_reason="end_of_window"`
        (CR-01 fix, REVIEW.md) — the same fill/commission arithmetic as a
        normal `_manage_day` close, so no position opened during the window
        is silently dropped from every reported metric. Not called
        automatically by `run()` (keeps `run()` pure/side-effect-scoped);
        `options_run.main` calls it once, right after `engine.run(...)`.
        Sets `self.open_positions_at_end` to whatever remains un-markable
        (e.g. no bar at all for a leg) so that residue is still visible.
        """
        for code, pos in list(self._open.items()):
            chain = self.chains[code]
            quotes = {}
            carried = False
            missing = False
            for leg in pos["legs"]:
                close = chain.bar_close(leg["code"], day)
                if close is None:
                    close = chain.last_known_close(leg["code"], day)
                    carried = True
                if close is None:
                    missing = True
                    break
                bid, ask = synthesize_bid_ask(close, self.spread_pct)
                quotes[leg["code"]] = {"bid": bid, "ask": ask}
            if missing:
                continue  # cannot mark this position at all -- leave it open

            net_exit = 0.0
            for leg in pos["legs"]:
                q = quotes[leg["code"]]
                mid = (q["bid"] + q["ask"]) / 2
                close_side = "BUY" if leg["side"] == "SELL" else "SELL"
                fill = leg_fill_price(mid, close_side, self.slippage_usd)
                net_exit += fill if leg["side"] == "SELL" else -fill

            close_commission = self.commission_per_leg * len(pos["legs"]) * pos["qty"]
            self._record_close(code, pos, day, "end_of_window", net_exit, close_commission, carried)

        self.open_positions_at_end = len(self._open)

    def _record_close(self, code, pos, day, exit_reason, net_exit,
                      close_commission, carried_mark) -> None:
        """Append one D-16-shaped trade-log row and drop the position from `self._open`.

        Realized P&L formula byte-for-byte equivalent to
        `bot/options/service.py:770-776` — `(credit - net_exit) * 100 * qty`.
        """
        credit = float(pos["credit_per_spread"])
        qty = pos["qty"]
        realized = (credit - net_exit) * _CONTRACT_MULTIPLIER * qty
        realized -= pos["open_commission_usd"] + close_commission

        today = date.fromisoformat(day)
        expiry = date.fromisoformat(pos["expiry"])
        dte_at_close = option_dte(expiry, today)

        strikes = {}
        for leg in pos["legs"]:
            side_word = "short" if leg["side"] == "SELL" else "long"
            right_word = "put" if leg["right"] == "P" else "call"
            strikes[f"{side_word}_{right_word}_strike"] = leg["strike"]

        self.trade_log.append({
            "symbol": code,
            "structure": pos["structure"],
            "opened_date": pos["opened_at"],
            "closed_date": day,
            "expiry": pos["expiry"],
            "short_put_strike": strikes.get("short_put_strike"),
            "long_put_strike": strikes.get("long_put_strike"),
            "short_call_strike": strikes.get("short_call_strike"),
            "long_call_strike": strikes.get("long_call_strike"),
            "credit_per_spread": credit,
            "qty": qty,
            "width": pos["width"],
            "exit_reason": exit_reason,
            "pnl_usd": realized,
            "max_loss_usd": pos["max_loss_usd"],
            "dte_at_open": pos["dte_at_entry"],
            "dte_at_close": dte_at_close,
            "ivr_at_entry": pos["ivr_at_entry"],
            "credit_captured_pct": (credit - net_exit) / credit * 100.0 if credit else 0.0,
            "commission_usd": pos["open_commission_usd"] + close_commission,
            "carried_mark": carried_mark,
            "min_leg_volume": pos.get("min_leg_volume"),
        })
        del self._open[code]
        self._opened_on.pop(code, None)

    # --------------------------------------------------------
    # Entry scan (D-13 — live gate order, `service.py:467-541`)
    # --------------------------------------------------------

    def _check_daily_breaker(self, day: str) -> None:
        """Trip the breaker for `day` when today's REALIZED + UNREALIZED P&L
        breaches the daily loss limit -- mirrors live's two trip points:
        `_job_entry_scan`'s realized-only call AND `_job_manage`'s
        realized+unrealized call (`bot/options/service.py:690,718`; WR-02).
        `_manage_day` (called before `_entry_scan` in `run_day`) has already
        populated `self._unrealized_today` for `day` by the time this runs;
        it defaults to 0.0 when `_entry_scan` is exercised directly without
        a preceding `_manage_day` call (e.g. in a unit test).
        """
        cfg = self.cfg
        realized_today = sum(
            t["pnl_usd"] for t in self.trade_log if t["closed_date"] == day
        )
        limit = -cfg.daily_loss_limit_pct / 100 * cfg.sizing_equity_usd
        if realized_today + self._unrealized_today > limit:
            return
        self._breaker_days.add(day)

    def _entry_scan(self, day: str, today, change_pct: dict) -> None:
        """Mirrors `_job_entry_scan` -> `_scan_and_open`'s exact order:
        breaker check (BEFORE the per-day cap) -> breaker-tripped check ->
        per-day cap -> per-underlying loop (`sorted(self.chains)`, break on
        cap, continue if already open) -> gate -> expiry -> strikes -> size.
        """
        cfg = self.cfg

        self._check_daily_breaker(day)
        if day in self._breaker_days:
            return

        opened_today = sum(1 for d in self._opened_on.values() if d == day)
        if opened_today >= cfg.max_new_positions_per_day:
            return

        open_max_loss_total = sum(p["max_loss_usd"] for p in self._open.values())
        open_count = len(self._open)

        for code in sorted(self.chains):
            if (opened_today >= cfg.max_new_positions_per_day
                    or open_count >= cfg.max_concurrent_positions):
                break
            if code in self._open:
                continue

            chain = self.chains[code]
            underlying_px = chain.underlying_close(day)
            if underlying_px is None:
                continue

            u = {
                "ivr_pct": iv_rank(self._iv_series.get(code, [])),
                "ivp_pct": None,
                "change_pct": change_pct.get(code),
            }
            if not passes_entry_gate(u, cfg):
                continue

            exp = pick_expiry(chain.expiries_for_day(day), today, cfg)
            if exp is None:
                continue

            exp_rows_raw = chain.rows_for(day, exp, underlying_px, self.strike_band_pct)
            rows = build_rows(exp_rows_raw, underlying_px, day, self.r,
                              self.spread_pct, self.oi_source, cfg.min_open_interest)

            sel = pick_strikes(rows, underlying_px, cfg.structure_type, cfg)
            if sel is None:
                continue

            qty = size_position(sel["width"], sel["credit"], cfg, open_max_loss_total)
            if qty < 1:
                continue

            vol_by_code = {r["code"]: r["bar_volume"] for r in rows}
            pos = self._open_position(code, sel, qty, exp, today, day, u["ivr_pct"], vol_by_code)

            opened_today += 1
            open_count += 1
            open_max_loss_total += pos["max_loss_usd"]

    def _open_position(self, code, sel, qty, exp, today, day, ivr_at_entry, vol_by_code) -> dict:
        """Fill each leg at `leg_fill_price`; the position dict mirrors
        `bot/options/service.py::_try_open`'s (`underlying`, `structure`,
        `expiry`, `dte_at_entry`, `ivr_at_entry`, `credit_per_spread`,
        `width`, `qty`, `max_loss_usd`, `opened_at`) plus `legs` and
        `open_commission_usd`. `credit_per_spread` is the FILL-adjusted
        credit (sum of SELL fills minus sum of BUY fills) — post-slippage,
        so downstream manage-time P&L math never needs a second conversion.
        """
        legs = []
        for leg in sel["legs"]:
            fill_price = leg_fill_price(leg["mid"], leg["side"], self.slippage_usd)
            legs.append({**leg, "fill_price": fill_price})

        credit_per_spread = (
            sum(l["fill_price"] for l in legs if l["side"] == "SELL")
            - sum(l["fill_price"] for l in legs if l["side"] == "BUY")
        )
        open_commission_usd = self.commission_per_leg * len(legs) * qty
        min_leg_volume = (
            min((vol_by_code.get(l["code"], 0) or 0) for l in legs) if legs else None
        )

        pos = {
            "underlying": code,
            "structure": self.cfg.structure_type,
            "expiry": exp.isoformat(),
            "dte_at_entry": option_dte(exp, today),
            "ivr_at_entry": ivr_at_entry,
            "credit_per_spread": credit_per_spread,
            "width": sel["width"],
            "qty": qty,
            "max_loss_usd": (sel["width"] - credit_per_spread) * _CONTRACT_MULTIPLIER * qty,
            "opened_at": day,
            "legs": legs,
            "open_commission_usd": open_commission_usd,
            "min_leg_volume": min_leg_volume,
        }
        self._open[code] = pos
        self._opened_on[code] = day
        return pos
