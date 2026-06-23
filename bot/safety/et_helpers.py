#!/usr/bin/env python3
"""
bot.safety.et_helpers — DST-correct US Eastern time helpers via zoneinfo.

Provides the canonical `ET` timezone object and two helpers — `now_et()` and
`to_et(dt)` — used by all strategy timing logic. Uses zoneinfo (stdlib) for
DST-correct America/New_York conversions without any third-party timezone libs
or fixed UTC offsets (SVC-04).

Exports: ET, now_et, to_et
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ============================================================
# Timezone Constant
# ============================================================

# Canonical Eastern timezone object (handles DST automatically)
# -5h (EST) in winter, -4h (EDT) in summer — never a fixed offset.
ET = ZoneInfo("America/New_York")


# ============================================================
# Public API
# ============================================================

def now_et() -> datetime:
    """Return the current time as a timezone-aware datetime in US Eastern.

    Always returns an aware datetime (utcoffset() is not None).
    DST transitions are handled automatically by zoneinfo.
    Never returns a naive datetime.
    """
    return datetime.now(tz=ET)


def to_et(dt: datetime) -> datetime:
    """Convert an aware datetime to US Eastern time.

    If dt is naive (no tzinfo), it is treated as UTC and converted to ET.
    This makes the behaviour explicit and consistent with PITFALLS #6: never
    silently assume the host timezone.

    dt: datetime — any datetime object (aware or naive).
    Returns an aware datetime in ET.
    Raises TypeError if dt is not a datetime instance.
    """
    if not isinstance(dt, datetime):
        raise TypeError(f"to_et() requires a datetime, got {type(dt).__name__}")
    if dt.tzinfo is None:
        # Treat naive as UTC per project convention (PITFALLS #6 mitigation)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)
