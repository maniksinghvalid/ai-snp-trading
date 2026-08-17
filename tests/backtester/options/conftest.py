#!/usr/bin/env python3
"""
tests.backtester.options.conftest — shared fixtures for Phase 9 offline
options-backtester tests. No live network anywhere (D-18).
"""
import pytest

from backtester.massive import MassiveDataSource

# Same delta/mid grid shape as tests/options/test_strategy.py::_row/_grid,
# adapted for backtest-derived (chain-row) fixtures rather than live rows.
_PUT_DELTAS = {
    99: -0.45, 98: -0.38, 97: -0.30, 96: -0.23, 95: -0.16,
    94: -0.11, 93: -0.07, 92: -0.05, 91: -0.03, 90: -0.02,
}
_CALL_DELTAS = {
    101: 0.45, 102: 0.38, 103: 0.30, 104: 0.23, 105: 0.16,
    106: 0.11, 107: 0.07, 108: 0.05, 109: 0.03, 110: 0.02,
}
_MIDS = {
    99: 2.00, 98: 1.60, 97: 1.20, 96: 0.85, 95: 0.50,
    94: 0.20, 93: 0.12, 92: 0.08, 91: 0.05, 90: 0.03,
    101: 2.00, 102: 1.60, 103: 1.20, 104: 0.85, 105: 0.50,
    106: 0.20, 107: 0.12, 108: 0.08, 109: 0.05, 110: 0.03,
}
_HALF_SPREAD = 0.01


def _row(strike, right, oi=1000, mid=None):
    mid = _MIDS[strike] if mid is None else mid
    delta = _PUT_DELTAS[strike] if right == "P" else _CALL_DELTAS[strike]
    return {
        "code": f"US.SPY260918{right}{int(strike * 1000):08d}",
        "right": right,
        "strike": float(strike),
        "delta": delta,
        "bid": round(mid - _HALF_SPREAD, 4),
        "ask": round(mid + _HALF_SPREAD, 4),
        "open_interest": oi,
    }


@pytest.fixture
def chain_grid():
    """Full 90-110 chain-row grid, keys matching the live screen_options shape
    (code, right, strike, delta, bid, ask, open_interest)."""
    def _make(overrides=None):
        overrides = overrides or {}
        rows = [_row(k, "P") for k in _PUT_DELTAS] + [_row(k, "C") for k in _CALL_DELTAS]
        for row in rows:
            row.update(overrides.get((int(row["strike"]), row["right"]), {}))
        return rows
    return _make


@pytest.fixture
def fake_massive(tmp_path):
    """Factory: build a MassiveDataSource whose _get_json is monkeypatched
    from a caller-supplied {url_substring: payload} map — zero live network.
    """
    def _make(payload_map):
        src = MassiveDataSource("test-key", cache_dir=str(tmp_path))

        def _get_json(url):
            for needle, payload in payload_map.items():
                if needle in url:
                    return payload
            raise AssertionError(f"fake_massive: no payload registered for URL {url!r}")

        src._get_json = _get_json
        return src
    return _make
