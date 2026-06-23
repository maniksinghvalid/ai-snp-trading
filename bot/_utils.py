#!/usr/bin/env python3
"""
bot._utils — Null-safe accessor helpers for reading moomoo SDK DataFrame rows.

Helpers copied verbatim out of skills/moomooapi/scripts/common.py (574-627).
D-02: the bot imports the moomoo SDK directly; no path-shim import of skills/.

Exports: safe_get, safe_float, safe_int, format_enum, safe_close
"""


# ============================================================
# Null-safe Accessors
# ============================================================

def safe_get(row, *keys, default=""):
    """Safely get a value from a DataFrame row or dict, supports multiple fallback keys"""
    for key in keys:
        val = row.get(key) if hasattr(row, 'get') else getattr(row, key, None)
        if val is not None:
            return val
    return default


def safe_float(val, default=0.0):
    """Safely convert to float, returns default for N/A, empty string, None, etc."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0):
    """Safely convert to int, returns default for N/A, empty string, None, etc.
    Handles numpy scalar types to avoid precision loss for large integers (e.g. 18-digit acc_id) via float64."""
    if val is None:
        return default
    # numpy scalar: extract native Python type to avoid float64 precision loss
    if hasattr(val, 'item'):
        val = val.item()
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default


def format_enum(val):
    """Format enum value as string"""
    if hasattr(val, "name"):
        return val.name
    return str(val)


# ============================================================
# Context Management Helpers
# ============================================================

def safe_close(ctx):
    """Safely close a context"""
    try:
        if ctx:
            ctx.close()
    except Exception:
        pass
