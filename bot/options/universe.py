#!/usr/bin/env python3
"""
bot.options.universe — read-only reader for the equity bot's premarket watchlist.

D-17: the super_bull_call strategy's daily universe is the equity Trend Join Long
bot's premarket watchlist (data/bot_state.db, daily_scan table), read cross-process
from the options bot. This module intentionally imports nothing from bot.state —
importing StateStore would pull a write-capable handle (and the equity migration
runner) into the options process, breaking the "options bot never writes to the
equity DB" invariant by construction rather than by discipline. The query shape
mirrors bot.state.store.StateStore.get_watchlist_codes exactly, re-implemented
here with a plain, read-only sqlite3 connection instead of imported.

"Read-only" means no writes to the DB's DATA: the only connect in this module
opens the file via a `mode=ro` URI, so any write statement raises
sqlite3.OperationalError. It does NOT mean zero filesystem I/O — SQLite may still
create the DB's `-wal`/`-shm` companion files on a read-only connection's first
touch of a WAL-mode DB if they are not already present, which needs the directory
(not the DB's rows) to be writable. In production this is a non-issue: the equity
bot's data/ directory is already writable and its WAL files already exist while
it is running.

Every error path (missing file, locked DB, corrupt file, missing table, empty
result) fails closed to [] with a structured warning log — never raises out of
read_equity_watchlist — so a bad equity DB read means zero bull-call entries that
day, not a crashed options bot.

Exports: read_equity_watchlist
"""
import sqlite3
from pathlib import Path

from bot.safety.logger import get_logger

_logger = get_logger(__name__)

# The live daily_scan table held 23 rows on 2026-09-17 (top-20-gap-ranked scan,
# SCAN-08); the cap is load-bearing, not a defensive guess — do not remove it.
_WATCHLIST_CAP = 20


def _connect_ro(db_path: str, timeout_s: float) -> sqlite3.Connection:
    """Open db_path via a read-only URI connection.

    This is the ONLY sqlite3.connect call in this module. `Path.resolve().as_uri()`
    percent-encodes the path so spaces/special characters cannot break the URI.
    `mode=ro` means a missing file raises sqlite3.OperationalError rather than
    being created (the fail-closed behavior D-17 wants), and any write attempted
    through the returned connection also raises OperationalError.
    """
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=timeout_s)


def read_equity_watchlist(
    db_path: str,
    scan_date_iso: str,
    cap: int = _WATCHLIST_CAP,
    timeout_s: float = 5.0,
) -> list:
    """Return today's equity premarket watchlist codes, capped and rank-ordered.

    Re-implements the query shape of StateStore.get_watchlist_codes (read-only,
    no import of bot.state) against `db_path` for `scan_date_iso` (an ET date
    string, YYYY-MM-DD), returning at most `cap` codes ordered by rank ASC.

    Fails closed to [] (with a structured warning log) on any sqlite3.Error —
    missing file, locked DB, corrupt file, or a DB without a daily_scan table —
    and also on an empty result for that date. No os.path.exists precheck: that
    would be a TOCTOU race and would not catch a locked or corrupt DB anyway
    (RESEARCH.md Anti-Patterns) — errors are caught around the actual connect
    and query instead.

    Args:
        db_path: path to the equity bot's SQLite DB (service.equity_state_db).
        scan_date_iso: ET calendar date string (YYYY-MM-DD) to scan for.
        cap: maximum number of codes to return (default 20, load-bearing).
        timeout_s: sqlite busy-timeout in seconds for the read-only connection.

    Returns:
        list[str]: Moomoo-format codes, rank ASC, capped at `cap`. [] on any
        failure or when no rows exist for that date.
    """
    try:
        conn = _connect_ro(db_path, timeout_s)
        try:
            rows = conn.execute(
                "SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC LIMIT ?",
                (scan_date_iso, cap),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Base class: covers "unable to open database file", "database is
        # locked", "no such table", "file is not a database", etc. — all
        # fail closed to [] rather than crashing the entry scan.
        _logger.warning(
            "options_watchlist_unavailable",
            db_path=db_path,
            scan_date=scan_date_iso,
            error=str(exc),
        )
        return []

    codes = [str(r[0]) for r in rows]
    if not codes:
        _logger.warning(
            "options_watchlist_empty",
            db_path=db_path,
            scan_date=scan_date_iso,
        )
    return codes
