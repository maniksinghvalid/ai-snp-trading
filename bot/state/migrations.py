#!/usr/bin/env python3
"""
bot/state/migrations.py — SQLite migration runner for the AI S&P Trading Bot.

Provides an ordered list of migration SQL scripts (MIGRATIONS) and a runner
(run_migrations) that uses PRAGMA user_version as a version gate to apply
only outstanding migrations — making startup idempotent.

Exports: MIGRATIONS, CURRENT_VERSION, run_migrations
"""

import sqlite3


# ============================================================
# Migration 0001 — Full v1 Schema
# ============================================================
#
# Tables:
#   positions   — open position state (one row per active position)
#   trades      — immutable append-only closed trade log
#   daily_scan  — premarket scan results (one row per code per scan date)
#   bar_cache   — last-N 5m bars per code for swing-low on restart
#   meta        — key/value store for bot-level persistent flags
#
# Design notes:
#   • All TEXT date/time fields use ISO-8601 strings (UTC).
#   • UNIQUE constraints on daily_scan(scan_date, code) and
#     bar_cache(code, time_key) prevent duplicate inserts and support
#     Phase 2 idempotent-scan and Phase 4 bar-cache upserts.
#   • position_id and trade_id use TEXT to accommodate broker-assigned IDs.

_MIGRATION_0001 = """
CREATE TABLE IF NOT EXISTS positions (
    position_id      TEXT PRIMARY KEY,
    code             TEXT NOT NULL,
    phase            TEXT NOT NULL DEFAULT 'OPEN',
    entry_price      REAL NOT NULL,
    initial_stop     REAL NOT NULL,
    trail_stop       REAL,
    full_quantity    INTEGER NOT NULL,
    remaining_quantity INTEGER NOT NULL,
    opened_at        TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id         TEXT PRIMARY KEY,
    position_id      TEXT NOT NULL,
    code             TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    exit_price       REAL NOT NULL,
    quantity         INTEGER NOT NULL,
    exit_reason      TEXT NOT NULL,
    r_multiple       REAL,
    closed_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_scan (
    scan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    gap_pct          REAL NOT NULL,
    rank             INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE(scan_date, code)
);

CREATE TABLE IF NOT EXISTS bar_cache (
    code             TEXT NOT NULL,
    time_key         TEXT NOT NULL,
    open             REAL NOT NULL,
    high             REAL NOT NULL,
    low              REAL NOT NULL,
    close            REAL NOT NULL,
    volume           INTEGER NOT NULL,
    UNIQUE(code, time_key)
);

CREATE TABLE IF NOT EXISTS meta (
    key              TEXT PRIMARY KEY,
    value            TEXT
);
"""

# ============================================================
# Migration 0002 — Extend daily_scan with Phase 2 rich context
# ============================================================
#
# Adds per-candidate context columns that Phase 3 reads from StateStore
# rather than recomputing (D-08). All new columns are nullable (no NOT NULL)
# because SQLite ALTER TABLE ADD COLUMN does not allow non-null defaults
# without a DEFAULT expression. gap_pct is NOT re-added (already in 0001).
#
# scan_pass examples: "premarket" | "intraday_1" | "intraday_2"
#
# WR-03: implemented as an idempotent callable (not a multi-statement SQL string).
# `executescript` issues an implicit COMMIT and runs each ALTER outside a single
# transaction, so a mid-script crash could commit some columns while user_version
# stays at 1 — re-running the script then fails with "duplicate column name" and
# permanently wedges startup. Guarding each ALTER with a PRAGMA table_info check
# makes partial re-application a no-op for already-added columns.

_DAILY_SCAN_0002_COLUMNS = (
    ("prior_day_high", "REAL"),
    ("prior_close", "REAL"),
    ("sma200", "REAL"),
    ("rvol_baseline", "REAL"),
    ("scan_pass", "TEXT"),
)


def _migration_0002(conn: sqlite3.Connection) -> None:
    """Add Phase 2 rich-context columns to daily_scan, idempotently (D-08, WR-03).

    Each ALTER is guarded by a column-existence check so re-running after a
    partial failure (some columns committed, user_version not yet bumped) is a
    no-op rather than a fatal "duplicate column name" error.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_scan)")}
    for col, decl in _DAILY_SCAN_0002_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE daily_scan ADD COLUMN {col} {decl}")


# ============================================================
# Migration List (index N corresponds to migration step N+1)
# ============================================================

MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,   # adds rich context columns to daily_scan (Phase 2, D-08)
]


CURRENT_VERSION = 2


# ============================================================
# Migration Runner
# ============================================================

def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply outstanding migrations to the given SQLite connection.

    Reads PRAGMA user_version to determine the current schema level.
    Applies only migrations from MIGRATIONS[user_version:] in order.
    After each migration, updates PRAGMA user_version and commits.

    Each migration step is either a SQL string (applied via executescript) or a
    callable taking the connection (applied directly). Callable migrations may
    guard their DDL for idempotency (WR-03) so re-running after a partial failure
    does not raise "duplicate column name". The user_version bump is committed in
    the same transaction as the migration body so the two never diverge.

    Calling run_migrations on an already-migrated DB is a no-op:
    if user_version >= CURRENT_VERSION, nothing is applied.

    Args:
        conn: An open sqlite3.Connection. The connection must not be in
              autocommit mode (the default for sqlite3 is deferred
              transactions, which is correct here).
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        if callable(migration):
            # Callable migrations execute statements on the open transaction
            # (no implicit COMMIT) so the PRAGMA bump below commits atomically
            # with the DDL.
            migration(conn)
        else:
            # SQL-string migrations: executescript issues an implicit COMMIT of
            # any pending work, then runs the script.
            conn.executescript(migration)
        conn.execute(f"PRAGMA user_version = {i}")
        conn.commit()
