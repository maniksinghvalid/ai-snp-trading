#!/usr/bin/env python3
"""
bot/state/store.py — SQLite StateStore and atomic JSON snapshot writer.

Provides:
  DEFAULT_DB_PATH   — default SQLite path (./data/bot_state.db)
  resolve_db_path() — returns BOT_STATE_DB env var or DEFAULT_DB_PATH
  StateStore        — opens a SQLite connection, runs migrations on open,
                      exposes close() and the raw conn for later phases
  atomic_write_json — temp-file + os.replace snapshot writer (D-10/PITFALLS #10)

All I/O is synchronous stdlib only (no third-party deps).
"""

import json
import os
import sqlite3
import stat
import tempfile

from bot.state.migrations import run_migrations


# ============================================================
# DB Path Resolution (D-09)
# ============================================================

DEFAULT_DB_PATH = os.path.join("data", "bot_state.db")


def resolve_db_path() -> str:
    """Return the active SQLite DB path.

    Checks the BOT_STATE_DB environment variable first (so tests can point
    at a tmp path via monkeypatch); falls back to DEFAULT_DB_PATH.

    Returns:
        str: Absolute or relative path to the SQLite DB file.
    """
    return os.getenv("BOT_STATE_DB", DEFAULT_DB_PATH)


# ============================================================
# Atomic JSON Writer (D-10 / PITFALLS #10)
# ============================================================

def atomic_write_json(path: str, data: dict) -> None:
    """Write *data* as JSON to *path* atomically using temp-file + os.replace.

    Protocol (write → fsync → parse-validate → replace → fsync dir):
      1. Create a temp file in the SAME directory as *path* (same filesystem,
         so os.replace is guaranteed to be atomic on POSIX).
      2. Write JSON via json.dump (ensures_ascii=False for Unicode safety).
      3. flush() + os.fsync(fd) on the temp file so its data blocks are
         durably on disk BEFORE the rename — without this, os.replace can
         leave the directory entry pointing at a file whose data was never
         flushed (zero-length/truncated) after a power loss or OS crash.
      4. Re-open the temp file and json.load it (parse-validate) — if the
         content is not valid JSON or was truncated, raise before swapping.
      5. Call os.replace(tmp, path) — atomic rename on POSIX.
      6. fsync the containing directory so the rename itself is durable.
      7. Set restrictive 0600 permissions on the written file (T-01-07).

    On any exception before step 5, the temp file is unlinked and the
    original *path* is left untouched — a crash mid-write never corrupts
    the prior state. fsync of the temp file (step 3) and the directory
    (step 6) extend that guarantee across OS/power crashes (D-10).

    Args:
        path: Target file path. The containing directory must exist.
        data: JSON-serialisable dict to write.

    Raises:
        Exception: Any exception from json.dump, json.load, or os.replace
                   is re-raised after unlinking the temp file.
    """
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        # Step 1-3: Write JSON to temp file, then durably persist its data
        # blocks to disk (flush + fsync) BEFORE the rename.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # durably persist temp file data before replace
        fd = None  # fdopen took ownership; mark as consumed

        # Step 4: Parse-validate before swapping
        with open(tmp, "r", encoding="utf-8") as f:
            json.load(f)  # raises json.JSONDecodeError if content is corrupt

        # Step 5: Atomic swap
        os.replace(tmp, path)
        tmp = None  # swap succeeded; mark tmp as consumed

        # Step 6: fsync the containing directory so the rename is durable
        # (os.replace guarantees atomicity of the rename, not its durability).
        try:
            dir_fd = os.open(dir_, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # some filesystems/platforms disallow directory fsync

        # Step 7: Restrictive permissions (owner read/write only)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass  # chmod failure is non-fatal; file is already written

    except Exception:
        # Clean up temp file if swap has not happened yet
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ============================================================
# StateStore
# ============================================================

class StateStore:
    """Durable SQLite persistence layer for the AI S&P Trading Bot.

    Opens a SQLite connection and applies any outstanding migrations on open,
    ensuring the full v1 schema (positions, trades, daily_scan, bar_cache,
    meta) is present before any caller uses the DB.

    The DB path is resolved via resolve_db_path() unless overridden at
    construction time — making the path overridable via BOT_STATE_DB env var
    for tests (D-09).

    Usage::

        store = StateStore()
        store.open()
        conn = store.conn   # bare sqlite3.Connection for use by later phases
        store.close()

    Or as a context manager::

        with StateStore() as store:
            store.conn.execute(...)
    """

    def __init__(self, db_path: str = None) -> None:
        """Initialise the StateStore.

        Args:
            db_path: Optional path override. If None, resolve_db_path() is
                     used (honours BOT_STATE_DB env var).
        """
        self._db_path = db_path if db_path is not None else resolve_db_path()
        self._conn = None

    # ============================================================
    # Public Interface
    # ============================================================

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the active sqlite3.Connection.

        Raises:
            RuntimeError: If open() has not been called yet.
        """
        if self._conn is None:
            raise RuntimeError(
                "StateStore is not open. Call open() before accessing conn."
            )
        return self._conn

    def open(self) -> "StateStore":
        """Open the SQLite connection and apply any outstanding migrations.

        Creates the parent directory (./data/ by default) if it does not
        exist. Calls run_migrations(conn) so the full v1 schema is present
        before the caller uses the DB.

        Returns:
            self — allows chaining: store = StateStore().open()

        Raises:
            Exception: Propagates any sqlite3 or OS error from connection
                       or migration.
        """
        parent = os.path.dirname(os.path.abspath(self._db_path))
        os.makedirs(parent, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path)
        # Enable WAL mode for better concurrency (non-breaking for tests)
        self._conn.execute("PRAGMA journal_mode=WAL")
        run_migrations(self._conn)
        return self

    def close(self) -> None:
        """Close the SQLite connection.

        Safe to call even if open() was never called — this is a no-op in
        that case.
        """
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            finally:
                self._conn = None

    # ============================================================
    # Context Manager Support
    # ============================================================

    def __enter__(self) -> "StateStore":
        """Open the store and return self for use in a with block."""
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Close the store on exit from a with block."""
        self.close()
        return False  # do not suppress exceptions

    # ============================================================
    # Phase 4 Position Accessors
    # ============================================================

    def upsert_position(self, pos) -> None:
        """Insert or update a PositionState row atomically (DB-first, Pitfall G).

        Uses INSERT ... ON CONFLICT(position_id) DO UPDATE so that both new
        positions (first fill, AWAITING_FILL → ACTIVE) and transition updates
        (ACTIVE → PARTIAL_TAKEN → BREAKEVEN → TRAILING → CLOSED) go through a
        single method. Commits immediately so that every FSM transition is
        durable before updating in-memory state (Pitfall G, RESEARCH.md).

        pos.phase must be a PositionPhase enum — .value is written as TEXT.
        pos.opened_at and pos.updated_at are datetime | None; isoformat() is
        used for the DB TEXT column (UTC ISO-8601 convention, D-09).

        Args:
            pos: A PositionState instance (bot.position.state). Not type-hinted
                 here to avoid a circular import at the store layer.
        """
        self._conn.execute(
            """INSERT INTO positions
               (position_id, code, phase, entry_price, initial_stop, trail_stop,
                full_quantity, remaining_quantity, entry_order_id, exit_order_id,
                avg_fill_price, opened_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(position_id) DO UPDATE SET
                 phase=excluded.phase,
                 trail_stop=excluded.trail_stop,
                 remaining_quantity=excluded.remaining_quantity,
                 exit_order_id=excluded.exit_order_id,
                 avg_fill_price=excluded.avg_fill_price,
                 updated_at=excluded.updated_at""",
            (
                pos.position_id,
                pos.code,
                pos.phase.value,        # PositionPhase enum → TEXT
                pos.entry_price,
                pos.initial_stop,
                pos.trail_stop,
                pos.full_quantity,
                pos.remaining_quantity,
                pos.entry_order_id,
                pos.exit_order_id,
                pos.avg_fill_price,
                pos.opened_at.isoformat() if pos.opened_at else None,
                pos.updated_at.isoformat() if pos.updated_at else None,
            ),
        )
        self._conn.commit()

    def get_open_positions(self) -> list:
        """Return all non-CLOSED position rows as dicts (for startup reconciliation).

        Fetches every row from the positions table where phase != 'CLOSED'.
        Returns a list of plain dicts (keyed by column name) so callers can
        reconstruct PositionState objects without depending on the store's
        sqlite3.Row factory setting.

        Returns:
            list: List of dicts, one per open/in-flight position row.
                  Empty list if no open positions exist.
        """
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE phase != 'CLOSED'"
        ).fetchall()
        # Reset row_factory so subsequent queries return plain tuples (default)
        self._conn.row_factory = None
        return [dict(r) for r in rows]
