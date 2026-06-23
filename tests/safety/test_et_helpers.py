#!/usr/bin/env python3
"""
tests.safety.test_et_helpers — Unit tests for bot.safety.et_helpers.

Verifies:
- ET constant is ZoneInfo("America/New_York")
- now_et() returns an aware datetime in ET
- to_et() converts UTC-aware datetimes to ET
- DST-correct offsets: -4h in summer (EDT), -5h in winter (EST)
- to_et() raises TypeError for non-datetime input
"""
import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.safety.et_helpers import ET, now_et, to_et


# ============================================================
# ET constant tests
# ============================================================

def test_et_is_america_new_york():
    """ET must be ZoneInfo('America/New_York'), never a fixed offset."""
    assert isinstance(ET, ZoneInfo)
    assert str(ET) == "America/New_York"


# ============================================================
# now_et() tests
# ============================================================

def test_now_et_is_aware():
    """now_et() must return an aware datetime (tzinfo is not None)."""
    result = now_et()
    assert isinstance(result, datetime.datetime)
    assert result.tzinfo is not None


def test_now_et_utcoffset_not_none():
    """now_et() utcoffset() must not be None — confirms awareness."""
    result = now_et()
    assert result.utcoffset() is not None


def test_now_et_zone_is_et():
    """now_et() tzinfo must reference the ET zone."""
    result = now_et()
    # The key is that DST is handled automatically; offset changes by season
    offset = result.utcoffset().total_seconds()
    # Valid ET offsets: -18000 (UTC-5, EST) or -14400 (UTC-4, EDT)
    assert offset in (-18000, -14400), f"Unexpected ET offset: {offset}"


# ============================================================
# to_et() DST-correctness tests
# ============================================================

def test_to_et_summer_is_minus_4h():
    """A July UTC noon must convert to ET with offset -4h (EDT)."""
    # 2026-07-01 12:00 UTC — clearly in daylight saving time
    utc_dt = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    et_dt = to_et(utc_dt)
    offset_seconds = et_dt.utcoffset().total_seconds()
    assert offset_seconds == -4 * 3600, (
        f"Expected -4h (EDT) in summer, got {offset_seconds / 3600}h"
    )


def test_to_et_winter_is_minus_5h():
    """A January UTC noon must convert to ET with offset -5h (EST)."""
    # 2026-01-01 12:00 UTC — clearly in standard time
    utc_dt = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    et_dt = to_et(utc_dt)
    offset_seconds = et_dt.utcoffset().total_seconds()
    assert offset_seconds == -5 * 3600, (
        f"Expected -5h (EST) in winter, got {offset_seconds / 3600}h"
    )


def test_to_et_preserves_moment():
    """to_et() must not change the absolute moment in time — only the representation."""
    utc_dt = datetime.datetime(2026, 7, 15, 14, 30, 0, tzinfo=datetime.timezone.utc)
    et_dt = to_et(utc_dt)
    # Both represent the same instant; converting back to UTC must match
    back_to_utc = et_dt.astimezone(datetime.timezone.utc)
    assert back_to_utc == utc_dt


def test_to_et_naive_treated_as_utc():
    """A naive datetime must be treated as UTC (not host-local) per PITFALLS #6."""
    naive_dt = datetime.datetime(2026, 7, 1, 12, 0, 0)  # no tzinfo
    et_dt = to_et(naive_dt)
    # Should produce same result as the UTC-aware equivalent
    utc_dt = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    et_reference = to_et(utc_dt)
    assert et_dt == et_reference


def test_to_et_already_aware_non_utc():
    """to_et() must correctly convert a non-UTC aware datetime."""
    # Pacific time (UTC-7 in summer, PDT)
    pdt = datetime.timezone(datetime.timedelta(hours=-7))
    pdt_dt = datetime.datetime(2026, 7, 1, 5, 0, 0, tzinfo=pdt)  # 05:00 PDT = 12:00 UTC
    et_dt = to_et(pdt_dt)
    # 12:00 UTC → 08:00 EDT (UTC-4)
    assert et_dt.hour == 8
    assert et_dt.minute == 0


def test_to_et_rejects_non_datetime():
    """to_et() must raise TypeError for non-datetime inputs."""
    with pytest.raises(TypeError):
        to_et("2026-07-01 12:00:00")

    with pytest.raises(TypeError):
        to_et(1234567890)


# ============================================================
# No fixed offsets / no pytz
# ============================================================

def test_no_fixed_offset_used():
    """Verify ET helpers use zoneinfo, not fixed timedelta offsets."""
    # If ET were a fixed UTC offset, summer and winter offsets would be identical.
    # This test verifies DST handling exists by showing they differ.
    summer_utc = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    winter_utc = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    summer_offset = to_et(summer_utc).utcoffset().total_seconds()
    winter_offset = to_et(winter_utc).utcoffset().total_seconds()
    assert summer_offset != winter_offset, (
        "Summer and winter offsets are the same — fixed offset detected!"
    )
