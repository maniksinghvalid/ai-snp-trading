#!/usr/bin/env python3
"""
tests/service/test_report.py — RED unit stubs for ReportBuilder (DASH-01).

These tests target bot.service.report.ReportBuilder which is implemented in plan 05-04.
They are xfail until that plan lands, so the suite collects cleanly and the
later wave has concrete verification targets to turn green.

Requirements covered:
  DASH-01 — EOD HTML report renders all required sections + SVG R-multiple histogram
"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


# ============================================================
# Helpers
# ============================================================

def _make_report_builder_with_mocks(tmp_path):
    """Return a ReportBuilder with a mock StateStore and tmp_path output directory.

    Raises ImportError (caught by xfail) until bot.service.report is implemented.
    """
    from bot.service.report import ReportBuilder  # noqa

    mock_store = MagicMock()
    # Simulate 3 closed trades (PnL data for histogram)
    mock_store.conn.execute.return_value.fetchall.return_value = [
        {"code": "US.AAPL", "r_multiple": 1.5, "closed_at": "2026-06-24T15:00:00"},
        {"code": "US.MSFT", "r_multiple": -0.5, "closed_at": "2026-06-24T14:00:00"},
        {"code": "US.TSLA", "r_multiple": 2.1, "closed_at": "2026-06-24T13:00:00"},
    ]

    builder = ReportBuilder(store=mock_store, report_dir=str(tmp_path))
    return builder


# ============================================================
# DASH-01: HTML report sections
# ============================================================

def test_html_report_sections(tmp_path):
    """build_daily_html must produce HTML with all required report sections (DASH-01)."""
    builder = _make_report_builder_with_mocks(tmp_path)

    html = builder.build_daily_html(session_date="2026-06-24")

    assert "<html" in html.lower(), "Output must be HTML"
    # Required sections (case-insensitive search)
    html_lower = html.lower()
    assert "trade" in html_lower, "HTML report must include a trades section"
    assert "pnl" in html_lower or "p&l" in html_lower or "profit" in html_lower, (
        "HTML report must include a PnL / profit-and-loss section"
    )
    assert "r-multiple" in html_lower or "r multiple" in html_lower or "histogram" in html_lower, (
        "HTML report must include an R-multiple histogram section"
    )


def test_r_histogram_buckets(tmp_path):
    """_build_r_histogram must produce correct bucket counts for test trade data (DASH-01)."""
    builder = _make_report_builder_with_mocks(tmp_path)

    # Three trades: R=1.5 (win), R=-0.5 (loss), R=2.1 (win)
    r_multiples = [1.5, -0.5, 2.1]
    buckets = builder._build_r_histogram(r_multiples)

    assert isinstance(buckets, dict), "Histogram must return a dict of bucket→count"
    # There must be at least 1 loss bucket (R=-0.5) and at least 1 win bucket (R=1.5 or 2.1)
    total_count = sum(buckets.values())
    assert total_count == 3, (
        f"Total histogram count must equal number of trades (3), got {total_count}"
    )
    # At least one negative bucket (loss)
    negative_buckets = {k: v for k, v in buckets.items() if float(k) < 0}
    assert len(negative_buckets) > 0, "Histogram must have at least one negative R bucket"
