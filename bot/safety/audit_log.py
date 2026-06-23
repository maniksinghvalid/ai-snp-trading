#!/usr/bin/env python3
"""
bot.safety.audit_log — Append-only JSONL audit writer for the trading bot.

Extends the existing ~/.futu_trade_audit.jsonl pattern (SAFE-05) used by
skills/moomooapi/scripts/trade/place_order.py. This module exposes a public
append_audit() function that adds a bot_version and UTC timestamp field, and
never truncates prior entries (always "a" open mode).

Exports: append_audit, AUDIT_LOG_PATH
"""
import datetime
import json
import os

# ============================================================
# Constants
# ============================================================

# Extends the existing skills audit log path (SAFE-05: append-only, never replace)
AUDIT_LOG_PATH = os.path.join(os.path.expanduser("~"), ".futu_trade_audit.jsonl")

# Bot version for traceability in audit entries
BOT_VERSION = "0.1.0"


# ============================================================
# Public API
# ============================================================

def append_audit(entry: dict) -> None:
    """Append a structured audit entry to the JSONL audit log.

    Extends the existing ~/.futu_trade_audit.jsonl (SAFE-05): same path,
    same "a" append mode (never overwrites), same ensure_ascii=False JSON
    encoding, same silent-on-failure behavior.

    Adds a UTC timestamp and bot_version field for traceability.

    entry: dict — arbitrary key-value pairs to record; "timestamp" and
        "bot_version" are injected automatically.
    """
    try:
        record = dict(entry)
        record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        record["bot_version"] = BOT_VERSION
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
