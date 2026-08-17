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
#   • All TEXT timestamp fields (*_at, *_time) use ISO-8601 strings in UTC.
#   • Session-scoped date keys (*_date, e.g. scan_date, session_date) use the
#     US Eastern Time (ET) calendar date (now_et().date().isoformat()), NOT UTC.
#     The strategy is ET-anchored so ET dates are the natural session boundary.
#     Using UTC dates would miskey late-afternoon scans relative to Phase 3 reads.
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

# ============================================================
# Migration 0003 — Daily Trade Counter + Pending Intents (Phase 3)
# ============================================================
#
# Tables:
#   daily_trade_count — authoritative filled-entry counter per session date.
#                       Incremented at fill time (Phase 4, D-08).
#                       Phase 3 reads filled_count for the D-09 daily-cap gate.
#   pending_intents   — one row per emitted OrderIntent (D-12).
#                       Lifecycle: PENDING → RESOLVED | EXPIRED (Phase 4 closes).
#
# WR-03: callable (not SQL string) so the DDL and the user_version bump commit
# atomically. Each statement uses conn.execute() — NOT executescript() — because
# executescript() issues an implicit COMMIT before executing, which means the
# DDL commits before the PRAGMA user_version bump in run_migrations(). A crash
# between _migration_0003 returning and the user_version bump would leave the
# tables present but user_version still at 2 (the WR-03 atomicity guarantee
# would be silently violated). Using conn.execute() keeps everything in the
# caller's open transaction. CREATE TABLE IF NOT EXISTS is idempotent without
# column-level guards.


def _migration_0003(conn: sqlite3.Connection) -> None:
    """Add Phase 3 daily-trade counter and pending-intent tables (D-08, D-12).

    Idempotent: each CREATE TABLE uses IF NOT EXISTS so re-running after a
    partial failure does not raise 'table already exists'. Uses conn.execute()
    (not executescript) so the DDL stays in the caller's open transaction and
    commits atomically with the PRAGMA user_version bump (WR-03).
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_trade_count (
        session_date    TEXT PRIMARY KEY,
        filled_count    INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_intents (
        intent_id       TEXT PRIMARY KEY,
        code            TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'PENDING',
        entry_price     REAL NOT NULL,
        stop_price      REAL NOT NULL,
        quantity        INTEGER NOT NULL,
        emitted_at      TEXT NOT NULL,
        resolved_at     TEXT
    )""")


# ============================================================
# Migration 0004 — Phase 4 order-tracking + FSM columns on positions
# ============================================================
#
# Adds three new columns to the positions table:
#   entry_order_id  — broker order_id from place_order() for the entry (EXEC-05)
#   exit_order_id   — broker order_id for any open exit order (nullable)
#   avg_fill_price  — actual average fill price for entry (D-06); used for R math
#
# The existing `phase` column (migration 0001) is string-compatible with
# PositionPhase enum values (ACTIVE, PARTIAL_TAKEN, BREAKEVEN, TRAILING, CLOSED).
#
# WR-03: callable migration with PRAGMA table_info guard on each ALTER so
# partial re-runs after a mid-failure are idempotent (same pattern as 0002).
# Uses conn.execute() (not executescript) for atomic commit with user_version bump.

_POSITIONS_0004_COLUMNS = (
    ("entry_order_id", "TEXT"),   # broker order_id for entry fill reconciliation (EXEC-05)
    ("exit_order_id",  "TEXT"),   # open exit order_id (nullable)
    ("avg_fill_price", "REAL"),   # actual fill price for R math (D-06)
)


def _migration_0004(conn: sqlite3.Connection) -> None:
    """Add Phase 4 FSM and order-tracking columns to positions table.

    Idempotent: each ALTER guarded by column-existence check (WR-03 pattern,
    same as _migration_0002). The existing `phase` column (migration 0001) is
    already present and string-compatible with PositionPhase enum values.
    Uses conn.execute() (not executescript) for atomic commit with user_version
    bump in run_migrations() (WR-03, see note at lines 143-152).

    D-08: never edit shipped migrations 0001-0003 — new column always in 0004.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0004_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")


# ============================================================
# Migration 0005 — Phase 7 tod_baselines table + broker_stop_order_id column
# ============================================================
#
# New table:
#   tod_baselines  — 14-session average cumulative session volume per
#                    (scan_date, code, time_bucket) for the RVOL-TOD gate.
#                    Composite PRIMARY KEY (scan_date, code, time_bucket) enforces
#                    one row per time bucket per candidate per scan date.
#
# New column on positions:
#   broker_stop_order_id — TEXT, nullable. Set to the broker-assigned order_id
#                          after place_stop_order() succeeds (D-01/D-04).
#                          NULL before the first stop is placed.
#
# WR-03: callable (not SQL string) so CREATE TABLE and ALTER TABLE commit
# atomically with the PRAGMA user_version bump in run_migrations().
# Uses conn.execute() not executescript() (same pattern as 0003/0004).
# CREATE TABLE IF NOT EXISTS is idempotent for the new table.
# PRAGMA table_info guard makes the ALTER TABLE idempotent (same as 0002/0004).

_POSITIONS_0005_COLUMNS = (
    ("broker_stop_order_id", "TEXT"),   # broker order_id of the live protective stop (D-01/D-04)
)


def _migration_0005(conn: sqlite3.Connection) -> None:
    """Add Phase 7 tod_baselines table + broker_stop_order_id column on positions.

    tod_baselines: stores 14-day average cumulative session volume per
    (scan_date, code, time_bucket) for the RVOL-TOD gate (SIG-RVOL-TOD).

    broker_stop_order_id: nullable column on positions — null before the first
    stop is placed; set to the broker order_id after place_stop_order() succeeds (D-01).

    Uses CREATE TABLE IF NOT EXISTS for the new table (idempotent without a guard).
    Uses PRAGMA table_info guard on the ALTER TABLE (WR-03 pattern from 0002/0004).
    Uses conn.execute() (not executescript) for atomic commit with user_version
    bump in run_migrations() (WR-03, see note at lines 143-152).

    D-08: never edit shipped migrations 0001-0004 — new objects always in 0005.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS tod_baselines (
        scan_date    TEXT NOT NULL,
        code         TEXT NOT NULL,
        time_bucket  TEXT NOT NULL,
        cum_vol_mean REAL NOT NULL,
        PRIMARY KEY (scan_date, code, time_bucket)
    )""")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0005_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")


# ============================================================
# Migration 0006 — Phase 8 options tables (tasty_credit_spreads)
# ============================================================
#
# New tables (used only by the options bot's own SQLite DB — the equity bot
# never reads them; both share this migration runner so a single schema level
# covers either DB file, D-06):
#   option_positions — one row per multi-leg credit spread (iron condor or
#                      put credit spread). max_loss_usd = (width - credit) * 100 * qty.
#   option_legs      — one row per single-leg option order making up a position.
#                      Moomoo has no combo/multi-leg order API, so legs are placed
#                      and tracked individually.
#
# Status vocabularies (plain TEXT, NOT CHECK constraints — the existing
# positions.phase column is likewise unconstrained; validation lives in Python):
#   option_positions.status : OPENING | OPEN | CLOSING | CLOSED | ABORTED | NEEDS_ATTENTION
#   option_legs.status      : PENDING | WORKING | FILLED | CLOSING | CLOSED | FAILED
#   option_legs."right"     : C | P
#   option_legs.side        : BUY | SELL
#
# Timestamp/date conventions follow the 0001 header: all *_at fields are
# ISO-8601 strings in UTC; expiry is an ISO date string (YYYY-MM-DD).
#
# The `right` column is quoted as "right" in the DDL because RIGHT is a join
# keyword in SQLite 3.39+; unquoted it is a parse hazard. Readers must use
# row["right"] (sqlite3.Row) — the column name itself is plain `right`.
#
# WR-03: callable (not SQL string) so the DDL commits atomically with the
# PRAGMA user_version bump in run_migrations(). Uses conn.execute() — NOT
# executescript() — for the same reason as 0003/0005 (see note at lines 143-152).
# CREATE TABLE/INDEX IF NOT EXISTS is idempotent without column-level guards.


def _migration_0006(conn: sqlite3.Connection) -> None:
    """Add Phase 8 option_positions + option_legs tables and the legs index.

    Idempotent: every statement uses IF NOT EXISTS, so re-running after a
    partial failure is a no-op rather than "table already exists". No DROP —
    rows written between two run_migrations calls survive.

    Uses conn.execute() (not executescript) so the DDL stays in the caller's
    open transaction and commits atomically with the PRAGMA user_version bump
    (WR-03, see note at lines 143-152).

    Note for readers: the leg option right is stored in a column named `right`,
    quoted in the DDL as "right" (SQLite 3.39+ join keyword). Read it as
    row["right"].

    D-08: never edit shipped migrations 0001-0005 — new objects always in 0006.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS option_positions (
        position_id       TEXT PRIMARY KEY,
        underlying        TEXT NOT NULL,
        structure         TEXT NOT NULL,
        expiry            TEXT NOT NULL,
        dte_at_entry      INTEGER,
        ivr_at_entry      REAL,
        credit_per_spread REAL,
        width             REAL,
        qty               INTEGER,
        max_loss_usd      REAL,
        status            TEXT NOT NULL,
        opened_at         TEXT,
        closed_at         TEXT,
        close_reason      TEXT,
        realized_pnl_usd  REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS option_legs (
        leg_id          TEXT PRIMARY KEY,
        position_id     TEXT NOT NULL,
        code            TEXT NOT NULL,
        "right"         TEXT NOT NULL,
        strike          REAL NOT NULL,
        side            TEXT NOT NULL,
        qty             INTEGER NOT NULL,
        entry_order_id  TEXT,
        entry_price     REAL,
        exit_order_id   TEXT,
        exit_price      REAL,
        status          TEXT NOT NULL
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_option_legs_position_id "
        "ON option_legs(position_id)"
    )


MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,   # adds rich context columns to daily_scan (Phase 2, D-08)
    _migration_0003,   # adds daily_trade_count + pending_intents (Phase 3, D-08/D-12)
    _migration_0004,   # adds entry_order_id, exit_order_id, avg_fill_price (Phase 4)
    _migration_0005,   # Phase 7: tod_baselines table + broker_stop_order_id on positions
    _migration_0006,   # Phase 8: option_positions + option_legs (+ legs index)
]


CURRENT_VERSION = 6


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
