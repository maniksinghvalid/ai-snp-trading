#!/usr/bin/env python3
"""
tests/signal/test_bar_aggregator.py — Tests for bot/signal/bar_aggregator.py

Verifies (no broker, no network — moomoo SDK base class is stubbed):
  (a) No signal fired mid-bar (time_key unchanged) — SIG-02
  (b) Signal fired exactly once when time_key advances — SIG-02
  (c) Session dedup prevents double-fire on reconnect re-push — SIG-02
  (d) HOD/LOD tracking updated correctly across bars — D-02, RESEARCH Pitfall 3
  (e) Malformed/corrupt push row is swallowed without crash — T-03-01
"""

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ============================================================
# Test helpers — stub the moomoo SDK base class so tests run without
# moomoo-api installed; the aggregator does the actual logic.
# ============================================================

class _StubCurKlineHandlerBase:
    """Minimal CurKlineHandlerBase stub for testing without moomoo-api."""

    def on_recv_rsp(self, rsp_pb):
        """Return the rsp_pb directly as (RET_OK, data) tuple."""
        return 0, rsp_pb  # RET_OK = 0


def _make_aggregator(loop, on_bar_closed):
    """Create a BarAggregator with the moomoo SDK base class patched out."""
    with patch.dict(
        "sys.modules",
        {"moomoo": MagicMock(
            CurKlineHandlerBase=_StubCurKlineHandlerBase,
            RET_OK=0,
        )},
    ):
        # Re-import with the stub in place.
        import importlib
        import bot.signal.bar_aggregator as _mod
        importlib.reload(_mod)
        return _mod.BarAggregator(loop=loop, on_bar_closed=on_bar_closed)


def _make_row(code="US.AAPL", time_key="2026-06-24 10:05:00",
              open_=150.0, high=155.0, low=149.0, close=154.0, volume=100000):
    """Return a fake single-row DataFrame matching the SDK push shape."""
    return pd.DataFrame([{
        "code": code,
        "time_key": time_key,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }])


def _make_aggregator_live(loop, on_bar_closed):
    """Create BarAggregator by patching moomoo at the module level."""
    mock_moomoo = MagicMock()
    mock_moomoo.CurKlineHandlerBase = _StubCurKlineHandlerBase
    mock_moomoo.RET_OK = 0

    with patch.dict("sys.modules", {"moomoo": mock_moomoo}):
        import importlib
        import bot.signal.bar_aggregator as _mod
        importlib.reload(_mod)
        agg = _mod.BarAggregator(loop=loop, on_bar_closed=on_bar_closed)
    return agg


# ============================================================
# Shared fixture: a fresh event loop + fire counter
# ============================================================

@pytest.fixture
def event_loop_and_counter():
    """Return a fresh asyncio event loop and a call-counter list."""
    loop = asyncio.new_event_loop()
    calls = []

    async def _on_bar_closed(bar_data):
        calls.append(bar_data)

    yield loop, _on_bar_closed, calls
    loop.close()


def _make_agg(loop, on_bar_closed):
    """Return a BarAggregator with the SDK base class stubbed out."""
    mock_moomoo = MagicMock()
    mock_moomoo.CurKlineHandlerBase = _StubCurKlineHandlerBase
    mock_moomoo.RET_OK = 0

    import importlib
    import sys
    original = sys.modules.get("moomoo")
    sys.modules["moomoo"] = mock_moomoo
    try:
        import bot.signal.bar_aggregator as _mod
        importlib.reload(_mod)
        agg = _mod.BarAggregator(loop=loop, on_bar_closed=on_bar_closed)
    finally:
        if original is None:
            del sys.modules["moomoo"]
        else:
            sys.modules["moomoo"] = original
    return agg


def _drain(loop, calls, expected_count, timeout=0.5):
    """Run the event loop briefly to drain asyncio tasks submitted via run_coroutine_threadsafe."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop.run_until_complete(asyncio.sleep(0.01))
        if len(calls) >= expected_count:
            break


# ============================================================
# SIG-02: mid-bar suppression
# ============================================================

class TestBarAggregatorMidBar:
    """Two pushes with the same time_key must never fire the on_bar_closed callback."""

    def test_no_signal_mid_bar(self, event_loop_and_counter):
        """
        Feeding two on_recv_rsp pushes with the SAME time_key for a code
        fires the on_bar_closed coroutine ZERO times (mid-bar updates never
        evaluate strategy — SIG-02).
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        row = _make_row(time_key="2026-06-24 10:05:00")
        agg.on_recv_rsp(row)  # first push — records time_key
        agg.on_recv_rsp(row)  # second push — same time_key, no advance

        _drain(loop, calls, expected_count=1, timeout=0.1)
        assert len(calls) == 0, (
            f"Mid-bar update must not fire on_bar_closed; got {len(calls)} call(s)"
        )


# ============================================================
# SIG-02: bar-close fires exactly once
# ============================================================

class TestBarAggregatorBarClose:
    """Pushing bar A then bar B (different time_keys) fires exactly once for bar A."""

    def test_bar_close_fires_once_on_advance(self, event_loop_and_counter):
        """
        Pushing bar A (time_key T1) then bar B (time_key T2) fires
        on_bar_closed exactly ONCE, carrying the CLOSED bar (T1) time_key.
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        bar_a = _make_row(time_key="2026-06-24 10:05:00", close=154.0)
        bar_b = _make_row(time_key="2026-06-24 10:10:00", close=156.0)

        agg.on_recv_rsp(bar_a)  # bar A first push — records T1
        agg.on_recv_rsp(bar_b)  # bar B first push — T1 closed, fire!

        _drain(loop, calls, expected_count=1, timeout=0.5)

        assert len(calls) == 1, (
            f"Should fire exactly once on time_key advance; got {len(calls)} call(s)"
        )
        assert calls[0]["time_key"] == "2026-06-24 10:05:00", (
            f"Fired for wrong bar; expected T1 but got {calls[0]['time_key']}"
        )


# ============================================================
# SIG-02: reconnect dedup
# ============================================================

class TestBarAggregatorReconnectDedup:
    """Re-pushing an already-seen time_key (reconnect scenario) must not double-fire."""

    def test_no_double_fire_on_reconnect(self, event_loop_and_counter):
        """
        After a bar closes and fires once, re-pushing that already-seen
        time_key via the reconnect re-push sequence does NOT fire again.

        Reconnect scenario per RESEARCH Pitfall 1 / SDK docs:
          "last data before disconnection will be pushed again" on is_first_push=True.

        Sequence:
          1. bar_a pushed → records T1 as _last_time_key.
          2. bar_b pushed → bar_a (T1) closes → fire #1. "T1" added to _seen_time_keys.
             _last_time_key = T2.
          3. Reconnect: SDK re-pushes bar_b (is_first_push=True, last cached = bar_b).
             _last_time_key = T2, bar_b time_key = T2 → SAME → mid-bar → no fire.
          4. bar_c pushed → bar_b (T2) closes → T2 NOT yet in _seen_time_keys → fire #2.
             Expected: fires for T2.
          5. Reconnect again: SDK re-pushes bar_c (last cached).
             bar_c time_key = T3, _last_time_key = T3 → SAME → no fire.
          6. bar_d pushed → bar_c (T3) closes → T3 added to _seen_time_keys → fire #3.
          7. Reconnect: SDK re-pushes bar_d (last cached).
             bar_d time_key = T4, _last_time_key = T4 → SAME → no fire.
          8. bar_c pushed AGAIN (e.g. stale push on reconnect) →
             prev = T4, incoming = T3 → different. closed = T4.
             T4 NOT in _seen_time_keys → would fire! But this is a different scenario.

        Correct reconnect dedup test: the SDK re-sends the SAME current bar
        (is_first_push=True), so _last_time_key == incoming time_key → mid-bar guard.
        Session-level _seen_time_keys is NOT reset on reconnect, only on reset_session().
        (SIG-02, Pitfall 1)
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        bar_a = _make_row(time_key="2026-06-24 10:05:00")
        bar_b = _make_row(time_key="2026-06-24 10:10:00")
        bar_c = _make_row(time_key="2026-06-24 10:15:00")

        # Step 1: bar_a records T1.
        agg.on_recv_rsp(bar_a)

        # Step 2: bar_b closes bar_a → fire #1.
        agg.on_recv_rsp(bar_b)
        _drain(loop, calls, expected_count=1, timeout=0.5)
        assert len(calls) == 1, "Expected exactly 1 fire after bar_a closes"

        # Step 3: SDK reconnect — re-pushes bar_b (is_first_push=True, last cached bar).
        # _last_time_key[code] == T2 and incoming is also T2 → same → mid-bar → no fire.
        agg.on_recv_rsp(bar_b)  # reconnect re-push of bar_b
        _drain(loop, calls, expected_count=2, timeout=0.1)
        assert len(calls) == 1, (
            f"Reconnect re-push of current bar (same time_key) must not fire; "
            f"expected 1 total, got {len(calls)}"
        )

        # Step 4: bar_c arrives normally → bar_b closes → fire #2.
        agg.on_recv_rsp(bar_c)
        _drain(loop, calls, expected_count=2, timeout=0.5)
        assert len(calls) == 2, "Expected 2 fires after bar_b closes"

        # Step 5: SDK reconnect again — re-pushes bar_c (last cached).
        # _last_time_key == T3, incoming T3 → same → no fire.
        agg.on_recv_rsp(bar_c)  # reconnect re-push of bar_c
        _drain(loop, calls, expected_count=3, timeout=0.1)
        assert len(calls) == 2, (
            f"Second reconnect re-push must also not fire; expected 2, got {len(calls)}"
        )


# ============================================================
# D-02: HOD/LOD running max/min tracking
# ============================================================

class TestBarAggregatorHodLod:
    """HOD and LOD on emitted BarEvent must be correct session running max/min."""

    def test_hod_lod_tracking(self, event_loop_and_counter):
        """
        HOD on the emitted BarEvent equals the running max of all pushed highs
        for that code; LOD equals the SESSION running-min of all pushed lows
        from the FIRST K_5M bar.

        Scenario: feed three bars where bar 2 holds the session-low and bar 3
        has a higher low — assert the emitted `lod` remains the bar-2 low
        (RESEARCH Pitfall 3 / Open-Q3 RESOLVED — LOD is the session running-min
        from the first bar, NOT the single current-bar low).
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        # Bar 1: high=155, low=149  → HOD=155, LOD=149
        bar1 = _make_row(time_key="2026-06-24 10:05:00", high=155.0, low=149.0)
        # Bar 2: high=158, low=147  → HOD=158, LOD=147 (session low)
        bar2 = _make_row(time_key="2026-06-24 10:10:00", high=158.0, low=147.0)
        # Bar 3: high=160, low=150  → HOD=160, LOD=147 (must remain bar-2 low)
        bar3 = _make_row(time_key="2026-06-24 10:15:00", high=160.0, low=150.0)
        # Bar 4: triggers bar-3 close
        bar4 = _make_row(time_key="2026-06-24 10:20:00", high=162.0, low=151.0)

        agg.on_recv_rsp(bar1)  # records T1
        agg.on_recv_rsp(bar2)  # T1 closes → fire #1
        _drain(loop, calls, expected_count=1, timeout=0.5)

        agg.on_recv_rsp(bar3)  # T2 closes → fire #2
        _drain(loop, calls, expected_count=2, timeout=0.5)

        agg.on_recv_rsp(bar4)  # T3 closes → fire #3
        _drain(loop, calls, expected_count=3, timeout=0.5)

        assert len(calls) == 3, f"Expected 3 bar-close events, got {len(calls)}"

        # Check fire #3 (bar3 close) carries correct session-level HOD and LOD.
        bar3_event = calls[2]
        assert bar3_event["time_key"] == "2026-06-24 10:15:00", (
            f"Unexpected time_key: {bar3_event['time_key']}"
        )
        # HOD must be the running max across all bars seen so far (bars 1-4 highs)
        # At the moment bar3 closes, the aggregator has seen highs 155, 158, 160, 162.
        assert bar3_event["hod"] >= 160.0, (
            f"HOD should be >= 160 (running max), got {bar3_event['hod']}"
        )
        # LOD must remain 147 — the bar-2 session low (not bar-3's 150 or bar-4's 151)
        assert bar3_event["lod"] == 147.0, (
            f"LOD must be session running-min 147 (bar-2 low), got {bar3_event['lod']}"
        )


# ============================================================
# CR-01 regression: closed BarEvent must carry the CLOSING bar's OHLCV
# ============================================================

class TestBarAggregatorNoRepaint:
    """SIG-02 no-repaint: emitted closed BarEvent must carry bar A's FINAL OHLCV,
    not bar B's first push values (CR-01 regression guard).

    Scenario: bar A goes through multiple mid-bar updates (close evolves),
    then bar B arrives with a DIFFERENT close. The BarEvent emitted for bar A
    must reflect bar A's FINAL close/open/high/low/volume — not bar B's values.
    HOD/LOD on the emitted event must also exclude bar B's first tick.
    """

    def test_closed_bar_carries_closing_bar_ohlcv_not_new_bars_first_tick(
        self, event_loop_and_counter
    ):
        """
        Push bar A three times (mid-bar updates with evolving close/high),
        then push bar B with distinct different values.

        The emitted BarEvent for bar A must carry bar A's FINAL values
        (from bar A's last push), NOT bar B's first-push values.

        Also: hod on the emitted event must equal bar A's running max (not
        bar B's high), and lod must equal the session min through bar A (not
        bar B's low).

        This is the core CR-01 regression test — if BarAggregator reverts to
        using the current row's OHLCV on advance, this test fails.
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        # Bar A — three mid-bar pushes with an evolving close/high.
        # T1, first push: close=150, high=152
        bar_a_push1 = _make_row(
            time_key="2026-06-24 10:05:00",
            open_=148.0, high=152.0, low=147.5, close=150.0, volume=100_000,
        )
        # T1, second push: close rises to 153, high to 154
        bar_a_push2 = _make_row(
            time_key="2026-06-24 10:05:00",
            open_=148.0, high=154.0, low=147.5, close=153.0, volume=200_000,
        )
        # T1, third (FINAL) push for bar A: close settles at 155, high=156
        bar_a_push3 = _make_row(
            time_key="2026-06-24 10:05:00",
            open_=148.0, high=156.0, low=147.0, close=155.0, volume=300_000,
        )

        # Bar B — first push arrives with a DISTINCT close (bar B's new bar open).
        # Use values clearly different from bar A's final values to make any
        # repaint immediately visible.
        bar_b_push1 = _make_row(
            time_key="2026-06-24 10:10:00",
            open_=157.0, high=159.0, low=156.5, close=158.0, volume=50_000,
        )

        # Feed bar A mid-bar updates (all same T1 → no emit).
        agg.on_recv_rsp(bar_a_push1)  # seeds T1
        agg.on_recv_rsp(bar_a_push2)  # mid-bar update
        agg.on_recv_rsp(bar_a_push3)  # mid-bar update (bar A final state)

        # Bar B's first push advances time_key → bar A closes.
        agg.on_recv_rsp(bar_b_push1)

        _drain(loop, calls, expected_count=1, timeout=0.5)

        assert len(calls) == 1, (
            f"Exactly one BarEvent should fire (for bar A); got {len(calls)}"
        )

        ev = calls[0]

        # time_key must be bar A's (not bar B's)
        assert ev["time_key"] == "2026-06-24 10:05:00", (
            f"BarEvent time_key must be bar A's T1, got {ev['time_key']!r}"
        )

        # OHLCV must match bar A's FINAL push values (bar_a_push3), NOT bar B's.
        assert ev["close"] == 155.0, (
            f"CR-01: close must be bar A's final close=155 (got {ev['close']}); "
            f"bar B's close=158 means repaint is occurring"
        )
        assert ev["open"] == 148.0, (
            f"CR-01: open must be bar A's open=148, got {ev['open']}"
        )
        assert ev["high"] == 156.0, (
            f"CR-01: high must be bar A's final high=156, got {ev['high']}; "
            f"bar B's high=159 means repaint is occurring"
        )
        assert ev["low"] == 147.0, (
            f"CR-01: low must be bar A's final low=147, got {ev['low']}; "
            f"bar B's low=156.5 means repaint is occurring"
        )
        assert ev["volume"] == 300_000, (
            f"CR-01: volume must be bar A's final volume=300000, got {ev['volume']}; "
            f"bar B's volume=50000 means repaint is occurring"
        )

        # HOD must equal bar A's running max (156 from bar_a_push3), NOT bar B's high (159).
        assert ev["hod"] == 156.0, (
            f"CR-01: hod must be bar A's session max=156 (excluding bar B); "
            f"got {ev['hod']}. bar B's high=159 means HOD includes bar B's tick."
        )

        # LOD must equal bar A's session min (147 from bar_a_push3), NOT bar B's low (156.5).
        assert ev["lod"] == 147.0, (
            f"CR-01: lod must be session min through bar A=147 (excluding bar B); "
            f"got {ev['lod']}. bar B's low=156.5 would give lod=147 anyway, but "
            f"a seed-only scenario would show the bug if bar B had a lower low."
        )


# ============================================================
# T-03-01: malformed push row
# ============================================================

class TestBarAggregatorMalformed:
    """A malformed push row must be swallowed without crash or callback fire."""

    def test_malformed_row_does_not_crash(self, event_loop_and_counter):
        """
        A push whose row is missing code/time_key or has non-numeric OHLC is
        swallowed (try/except) and fires nothing (RESEARCH Security: Tampering
        mitigation, T-03-01).
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        # Malformed row 1: missing code
        bad1 = pd.DataFrame([{"time_key": "2026-06-24 10:05:00", "high": "not_a_float"}])
        agg.on_recv_rsp(bad1)  # must not raise

        # Malformed row 2: missing time_key
        bad2 = pd.DataFrame([{"code": "US.AAPL", "high": 155.0}])
        agg.on_recv_rsp(bad2)  # must not raise

        # Malformed row 3: completely empty row
        bad3 = pd.DataFrame([{}])
        agg.on_recv_rsp(bad3)  # must not raise

        _drain(loop, calls, expected_count=1, timeout=0.1)
        assert len(calls) == 0, (
            f"Malformed rows must not fire on_bar_closed; got {len(calls)} call(s)"
        )


# ============================================================
# Phase 7 SIG-RVOL-TOD: cumulative session volume (cum_volume)
# ============================================================

class TestCumulativeSessionVolume:
    """BarEvent.cum_volume and BarAggregator._session_volume accumulator (Phase 7 SIG-RVOL-TOD).

    Verified behaviors:
      (a) BarEvent defaults cum_volume=0 so existing positional constructions remain valid.
      (b) A sequence of closed bars for one code accumulates cum_volume monotonically.
      (c) Two codes accumulate independently (per-code isolation).
      (d) reset_session() clears _session_volume — next closed bar restarts at its own volume.
    """

    def test_bar_event_cum_volume_defaults_to_zero(self):
        """BarEvent constructed without cum_volume argument must have cum_volume=0 (backward compat)."""
        from bot.signal.events import BarEvent
        event = BarEvent(
            code="US.AAPL",
            time_key="2026-07-03 10:05:00",
            open=150.0,
            high=155.0,
            low=149.0,
            close=154.0,
            volume=100_000,
            hod=155.0,
            lod=149.0,
        )
        assert event.cum_volume == 0, (
            f"BarEvent without cum_volume must default to 0; got {event.cum_volume}"
        )

    def test_cum_volume_accumulates_monotonically(self, event_loop_and_counter):
        """Feeding successive closed bars for one code makes cum_volume increase by each bar's volume.

        Three bars with volumes 100_000, 200_000, 300_000 must emit:
          bar1.cum_volume = 100_000
          bar2.cum_volume = 300_000 (100_000 + 200_000)
          bar3.cum_volume = 600_000 (300_000 + 300_000)
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        bar1 = _make_row(time_key="2026-07-03 10:05:00", volume=100_000)
        bar2 = _make_row(time_key="2026-07-03 10:10:00", volume=200_000)
        bar3 = _make_row(time_key="2026-07-03 10:15:00", volume=300_000)
        bar4 = _make_row(time_key="2026-07-03 10:20:00", volume=50_000)

        agg.on_recv_rsp(bar1)  # seeds T1 — no emit yet
        agg.on_recv_rsp(bar2)  # T1 closes (vol=100_000) → fire #1
        _drain(loop, calls, expected_count=1, timeout=0.5)

        agg.on_recv_rsp(bar3)  # T2 closes (vol=200_000) → fire #2
        _drain(loop, calls, expected_count=2, timeout=0.5)

        agg.on_recv_rsp(bar4)  # T3 closes (vol=300_000) → fire #3
        _drain(loop, calls, expected_count=3, timeout=0.5)

        assert len(calls) == 3, f"Expected 3 bar-close events, got {len(calls)}"

        assert calls[0]["cum_volume"] == 100_000, (
            f"bar1 cum_volume must be 100_000 (first bar), got {calls[0]['cum_volume']}"
        )
        assert calls[1]["cum_volume"] == 300_000, (
            f"bar2 cum_volume must be 300_000 (100k+200k), got {calls[1]['cum_volume']}"
        )
        assert calls[2]["cum_volume"] == 600_000, (
            f"bar3 cum_volume must be 600_000 (300k+300k), got {calls[2]['cum_volume']}"
        )
        # Verify per-bar volume field is unchanged (still the individual bar's volume)
        assert calls[2]["volume"] == 300_000, (
            f"Individual bar volume must be 300_000 (not cumulative), got {calls[2]['volume']}"
        )

    def test_cum_volume_is_per_code_isolated(self, event_loop_and_counter):
        """Two codes must accumulate cum_volume independently; one code's bars don't affect the other."""
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        aapl1 = _make_row(code="US.AAPL", time_key="2026-07-03 10:05:00", volume=100_000)
        aapl2 = _make_row(code="US.AAPL", time_key="2026-07-03 10:10:00", volume=200_000)
        msft1 = _make_row(code="US.MSFT", time_key="2026-07-03 10:05:00", volume=50_000)
        msft2 = _make_row(code="US.MSFT", time_key="2026-07-03 10:10:00", volume=75_000)
        aapl3 = _make_row(code="US.AAPL", time_key="2026-07-03 10:15:00", volume=10_000)
        msft3 = _make_row(code="US.MSFT", time_key="2026-07-03 10:15:00", volume=10_000)

        # Interleave AAPL and MSFT feeds — each code must accumulate its own session volume
        agg.on_recv_rsp(aapl1)   # seeds AAPL T1
        agg.on_recv_rsp(msft1)   # seeds MSFT T1
        agg.on_recv_rsp(aapl2)   # AAPL T1 closes (vol=100_000) → fire
        _drain(loop, calls, expected_count=1, timeout=0.5)
        agg.on_recv_rsp(msft2)   # MSFT T1 closes (vol=50_000) → fire
        _drain(loop, calls, expected_count=2, timeout=0.5)
        agg.on_recv_rsp(aapl3)   # AAPL T2 closes (vol=200_000) → fire
        _drain(loop, calls, expected_count=3, timeout=0.5)
        agg.on_recv_rsp(msft3)   # MSFT T2 closes (vol=75_000) → fire
        _drain(loop, calls, expected_count=4, timeout=0.5)

        assert len(calls) == 4, f"Expected 4 bar-close events, got {len(calls)}"

        # Separate calls by code
        aapl_calls = [c for c in calls if c["code"] == "US.AAPL"]
        msft_calls = [c for c in calls if c["code"] == "US.MSFT"]

        assert len(aapl_calls) == 2, f"Expected 2 AAPL closes, got {len(aapl_calls)}"
        assert len(msft_calls) == 2, f"Expected 2 MSFT closes, got {len(msft_calls)}"

        # AAPL: 100_000, then 300_000 (100k+200k)
        assert aapl_calls[0]["cum_volume"] == 100_000, (
            f"AAPL bar1 cum_volume must be 100_000, got {aapl_calls[0]['cum_volume']}"
        )
        assert aapl_calls[1]["cum_volume"] == 300_000, (
            f"AAPL bar2 cum_volume must be 300_000, got {aapl_calls[1]['cum_volume']}"
        )
        # MSFT: 50_000, then 125_000 (50k+75k)
        assert msft_calls[0]["cum_volume"] == 50_000, (
            f"MSFT bar1 cum_volume must be 50_000, got {msft_calls[0]['cum_volume']}"
        )
        assert msft_calls[1]["cum_volume"] == 125_000, (
            f"MSFT bar2 cum_volume must be 125_000, got {msft_calls[1]['cum_volume']}"
        )

    def test_reset_session_clears_cum_volume(self, event_loop_and_counter):
        """reset_session() must clear _session_volume so the next bar restarts from its own volume.

        Sequence:
          1. Feed two bars (cum_volume = 100k + 200k = 300k after bar1 closes).
          2. Call reset_session().
          3. Feed two more bars for the same code.
          4. The first bar after reset must have cum_volume == just that bar's volume.

        This is Pitfall 1 (RESEARCH): cross-session carryover would give a wrong RVOL-TOD
        numerator on day 2 — reset_session() must be idempotent and zero-out the accumulator.
        """
        loop, on_bar_closed, calls = event_loop_and_counter
        agg = _make_agg(loop, on_bar_closed)

        # Session 1: two bars
        bar1 = _make_row(code="US.AAPL", time_key="2026-07-03 10:05:00", volume=100_000)
        bar2 = _make_row(code="US.AAPL", time_key="2026-07-03 10:10:00", volume=200_000)
        bar3 = _make_row(code="US.AAPL", time_key="2026-07-03 10:15:00", volume=999)  # trigger bar2 close

        agg.on_recv_rsp(bar1)
        agg.on_recv_rsp(bar2)  # closes bar1
        _drain(loop, calls, expected_count=1, timeout=0.5)

        agg.on_recv_rsp(bar3)  # closes bar2
        _drain(loop, calls, expected_count=2, timeout=0.5)

        # bar2 close must carry the session-cumulative volume (100k + 200k = 300k)
        assert calls[1]["cum_volume"] == 300_000, (
            f"Before reset, bar2 cum_volume must be 300_000; got {calls[1]['cum_volume']}"
        )

        # --- reset session (simulates start of new trading day) ---
        agg.reset_session()

        # Session 2: two more bars — cum_volume must restart from the first bar's own volume
        bar4 = _make_row(code="US.AAPL", time_key="2026-07-04 10:05:00", volume=555_000)
        bar5 = _make_row(code="US.AAPL", time_key="2026-07-04 10:10:00", volume=111_000)
        bar6 = _make_row(code="US.AAPL", time_key="2026-07-04 10:15:00", volume=1)

        agg.on_recv_rsp(bar4)
        agg.on_recv_rsp(bar5)  # closes bar4 → must be 555_000 (NOT 300_000 + 555_000)
        _drain(loop, calls, expected_count=3, timeout=0.5)

        agg.on_recv_rsp(bar6)  # closes bar5
        _drain(loop, calls, expected_count=4, timeout=0.5)

        assert len(calls) == 4, f"Expected 4 total bar-close events, got {len(calls)}"

        # After reset, bar4 close must carry ONLY bar4's volume (no carryover from session 1)
        assert calls[2]["cum_volume"] == 555_000, (
            f"After reset_session, bar4 cum_volume must be 555_000 (no carryover), "
            f"got {calls[2]['cum_volume']}"
        )
        # bar5 close must carry 555_000 + 111_000 = 666_000
        assert calls[3]["cum_volume"] == 666_000, (
            f"After reset_session, bar5 cum_volume must be 666_000, "
            f"got {calls[3]['cum_volume']}"
        )
