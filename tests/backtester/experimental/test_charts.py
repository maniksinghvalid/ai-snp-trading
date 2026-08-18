#!/usr/bin/env python3
"""Tests for backtester.experimental.charts (plan 10-05, Task 2)."""
from backtester.experimental.charts import bars_svg, equity_svg


def test_equity_svg_one_polyline_per_series_and_svg_root():
    series_by_label = {
        "arm_a": [("2023-01-01", 100000.0), ("2023-01-02", 100500.0), ("2023-01-03", 100200.0)],
        "arm_b": [("2023-01-01", 100000.0), ("2023-01-02", 99500.0), ("2023-01-03", 99900.0)],
        "arm_c": [("2023-01-01", 100000.0)],
    }
    svg = equity_svg(series_by_label, title="test equity")
    assert svg.startswith("<svg") or "<svg" in svg
    assert svg.count("<polyline") == 3


def test_equity_svg_empty_series_dict_never_raises():
    svg = equity_svg({}, title="empty")
    assert "<svg" in svg
    assert svg.count("<polyline") == 0


def test_equity_svg_flat_series_never_divides_by_zero():
    svg = equity_svg({"flat": [("2023-01-01", 100000.0), ("2023-01-02", 100000.0)]}, title="flat")
    assert "<svg" in svg
    assert svg.count("<polyline") == 1


def test_bars_svg_emits_hline_reference_at_1_0():
    values_by_group = {
        "E1": {"arm_a": 1.2, "arm_b": 0.8},
        "E2": {"arm_a": 0.9, "arm_b": 1.5},
    }
    svg = bars_svg(values_by_group, title="test bars", hline=1.0)
    assert "<svg" in svg
    assert 'class="hline"' in svg
    assert ">1.0<" in svg


def test_bars_svg_handles_infinite_pf_without_raising_or_crashing_layout():
    values_by_group = {"E1": {"arm_a": float("inf"), "arm_b": 0.5}}
    svg = bars_svg(values_by_group, title="inf test", hline=1.0)
    assert "<svg" in svg
    assert 'class="hline"' in svg
    # Two bars rendered (one clipped, one normal) -- never raises, never an empty rect set.
    assert svg.count("<rect") == 2
