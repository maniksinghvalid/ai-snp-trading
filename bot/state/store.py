#!/usr/bin/env python3
"""
bot/state/store.py — SQLite StateStore and atomic JSON snapshot writer.

Provides:
  DEFAULT_DB_PATH   — default SQLite path (./data/bot_state.db)
  resolve_db_path() — returns BOT_STATE_DB env var or DEFAULT_DB_PATH
  StateStore        — opens a SQLite connection, runs migrations on open,
                      and exposes all DB operations as guarded methods
  atomic_write_json — temp-file + os.replace snapshot writer (D-10/PITFALLS #10)

All I/O is synchronous stdlib only (no third-party deps).

Enforced invariant (T-06.1-09-01/02, IN-01):
  ALL database access is routed through guarded StateStore methods.  Each method
  acquires ``with self._lock:`` around a short, purely-synchronous block
  (conn.execute + commit, plus any row_factory flip/reset).  The raw ``conn``
  property is DEPRECATED for external callers — use a guarded method instead.
  The lock is NEVER held across an async yield; the reconcile_once /
  startup_reconcile broker I/O (async subscribe / _derive_lod_for_orphan calls)
  sits OUTSIDE any guarded method call (CR-02; zero async tokens in this module).
"""

import json
import os
import sqlite3
import stat
import tempfile
import threading

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
        # RLock serializes all connection access across threads.
        # Re-entrant so a caller holding the lock can call another method that
        # also acquires it (e.g. an executor job calling a store read method).
        self._lock = threading.RLock()

    # ============================================================
    # Public Interface
    # ============================================================

    @property
    def conn(self) -> sqlite3.Connection:
        """DEPRECATED — internal/read-debug only.

        MUST NOT be used for ``.execute``, ``.commit``, or ``.row_factory``
        by any caller OUTSIDE ``bot/state/store.py``.  Use a guarded StateStore
        method instead (WR-02, T-06.1-09-04).

        Retained for backward compatibility and test/inspection use only.
        All production paths MUST route through a guarded method so the
        ``self._lock`` is always held around the DB operation.

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

        # Disable the same-thread check: the connection is intentionally shared
        # across the asyncio event-loop thread AND ThreadPoolExecutor worker
        # threads (the four scheduled executor jobs).
        #
        # Enforced invariant (IN-01 / T-06.1-09-01/02):
        #   ALL DB access is routed through guarded StateStore methods.  Each
        #   method acquires ``with self._lock:`` around a short synchronous
        #   block (conn.execute + commit, plus any row_factory flip/reset).
        #   The raw ``conn`` is NOT mutated outside this module.  The lock is
        #   NEVER held across an async yield — reconcile_once / startup_reconcile
        #   broker I/O sits between guarded calls, never inside one (CR-02).
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # WR-03: busy_timeout so contended writes wait ~5s instead of raising
        # OperationalError: database is locked (defense-in-depth atop serialization).
        self._conn.execute("PRAGMA busy_timeout=5000")
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
    # Thread-safety helpers
    # ============================================================

    def lock(self) -> threading.RLock:
        """Return the store's re-entrant lock as a context manager.

        Executor jobs must acquire this lock around ALL store access so that
        worker-thread operations are serialized against event-loop-thread
        manager/engine writes.  The lock is re-entrant (RLock), so a caller
        that already holds it (e.g. an executor job that calls a store method
        which also acquires it internally) does not deadlock.

        Usage::

            with self._store.lock():
                codes = self._store.get_watchlist_codes(today)

        Returns:
            The threading.RLock instance (usable as ``with store.lock(): ...``).
        """
        return self._lock

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
        with self._lock:
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

    def get_closed_trades(self, session_date) -> list:
        """Return last-20 closed trade rows for a given session date.

        Queries the trades table for rows WHERE DATE(closed_at) = session_date,
        ordered by closed_at DESC, limited to 20 rows (RESEARCH Open Q1).

        Uses the row_factory → fetchall → reset pattern (same as get_open_positions).
        NULL r_multiple values are preserved as-is; callers guard with `or 0.0`.

        session_date: date or str — the trading session date in YYYY-MM-DD format.
        Returns:
            list: List of dicts, one per closed trade row. Empty list if none.
        """
        with self._lock:
            # Atomically flip row_factory → fetch → reset so a concurrent thread
            # cannot observe or clobber the factory setting (T-06.1-08-02).
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE DATE(closed_at) = ? ORDER BY closed_at DESC LIMIT 20",
                (str(session_date),),
            ).fetchall()
            self._conn.row_factory = None
        return [dict(r) for r in rows]

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
        with self._lock:
            # Atomically flip row_factory → fetch → reset so a concurrent thread
            # cannot observe or clobber the factory setting (T-06.1-08-02).
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE phase != 'CLOSED'"
            ).fetchall()
            # Reset row_factory so subsequent queries return plain tuples (default)
            self._conn.row_factory = None
        return [dict(r) for r in rows]

    # ============================================================
    # Position-row mutation methods (CR-01 / T-06.1-09-01)
    # ============================================================

    def mark_position_closed(self, position_id: str, updated_at: str) -> None:
        """Mark a position row as CLOSED in the DB (guarded write).

        Replaces gateway.py raw UPDATE sites (reconcile_once line ~840-844 and
        startup_reconcile line ~1030-1033).  Each call acquires self._lock and
        commits immediately so the DB reflects the CLOSED state before the
        caller continues (CR-01).

        Args:
            position_id: The position_id UUID string to close.
            updated_at:  ISO-8601 timestamp string (ET) for updated_at column.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET phase='CLOSED', updated_at=? WHERE position_id=?",
                (updated_at, position_id),
            )
            self._conn.commit()

    def update_position_qty_phase(
        self, position_id: str, remaining_quantity: int, phase: str, updated_at: str
    ) -> None:
        """Update remaining_quantity AND phase on a position row (guarded write).

        Replaces gateway.py raw UPDATE at reconcile_once line ~878-883 (qty/phase
        drift re-arm path).

        Args:
            position_id:        The position_id UUID string.
            remaining_quantity: New remaining quantity (broker truth).
            phase:              New phase string (e.g. 'ACTIVE').
            updated_at:         ISO-8601 timestamp string (ET).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET remaining_quantity=?, phase=?, updated_at=? "
                "WHERE position_id=?",
                (remaining_quantity, phase, updated_at, position_id),
            )
            self._conn.commit()

    def update_position_qty(
        self, position_id: str, remaining_quantity: int, updated_at: str
    ) -> None:
        """Update remaining_quantity on a position row (guarded write).

        Replaces gateway.py raw UPDATE at startup_reconcile line ~1050-1054
        (startup qty adopt path).

        Args:
            position_id:        The position_id UUID string.
            remaining_quantity: New remaining quantity (broker truth).
            updated_at:         ISO-8601 timestamp string (ET).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET remaining_quantity=?, updated_at=? "
                "WHERE position_id=?",
                (remaining_quantity, updated_at, position_id),
            )
            self._conn.commit()

    def insert_orphan_position(
        self,
        position_id: str,
        code: str,
        avg_cost: float,
        stop: float,
        qty: int,
        now_ts: str,
    ) -> int:
        """INSERT OR IGNORE an orphan broker position row (guarded write).

        Replaces the raw INSERT at gateway.py reconcile_once ~917-926 and
        startup_reconcile ~1084-1101.  Returns cursor.rowcount so the caller
        can detect the WR-04 INSERT OR IGNORE no-op (rowcount == 0 means the
        row already existed; caller skips audit).

        Args:
            position_id: New UUID for the orphan position.
            code:        Moomoo-format stock code (e.g. "US.AAPL").
            avg_cost:    Broker average cost (used as entry_price and avg_fill_price).
            stop:        Derived stop from LOD (initial_stop and trail_stop).
            qty:         Broker-reported quantity (full_quantity and remaining_quantity).
            now_ts:      ISO-8601 timestamp string (ET) for opened_at and updated_at.

        Returns:
            int: cursor.rowcount (1 if inserted, 0 if the INSERT OR IGNORE was a no-op).
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO positions
                   (position_id, code, phase, entry_price, initial_stop, trail_stop,
                    full_quantity, remaining_quantity, entry_order_id,
                    avg_fill_price, opened_at, updated_at)
                   VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (position_id, code, avg_cost, stop, stop, qty, qty, avg_cost, now_ts, now_ts),
            )
            self._conn.commit()
            return cursor.rowcount

    # ============================================================
    # Row_factory-flipping read methods (CR-01 / T-06.1-09-01)
    # ============================================================

    def get_pending_intent_codes(self, status: str = "PENDING") -> list:
        """Return list of dicts [{"intent_id":..., "code":...}] for pending intents.

        Replaces the dangerous unguarded row_factory flip at gateway.py
        startup_reconcile ~1129-1133.  The flip → fetch → reset is done INSIDE
        the lock so no concurrent thread can observe or clobber the factory
        setting (T-06.1-09-01 / CR-01).

        Args:
            status: Intent status to filter on (default "PENDING").

        Returns:
            list: List of dicts with keys ``intent_id`` and ``code``.
                  Empty list if no matching rows.
        """
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT intent_id, code FROM pending_intents WHERE status=?",
                (status,),
            ).fetchall()
            self._conn.row_factory = None
        return [{"intent_id": r["intent_id"], "code": r["code"]} for r in rows]

    def get_filled_count(self, session_date: str) -> int:
        """Return filled_count for the given session date from daily_trade_count.

        Replaces signal_engine.py _get_filled_count body (~267-271).  Uses a
        plain tuple fetch (no row_factory flip needed for a single scalar).

        Args:
            session_date: ISO date string (e.g. "2026-06-24").

        Returns:
            int: filled_count, or 0 if no row exists yet.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT filled_count FROM daily_trade_count WHERE session_date=?",
                (session_date,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def has_pending_intent(self, code: str) -> bool:
        """Return True if a live PENDING intent exists for the given code (D-10).

        Replaces signal_engine.py has_pending_intent body (~286-290).  Uses a
        plain 1-column fetch (no row_factory flip).

        Args:
            code: Moomoo-format stock code (e.g. "US.AAPL").

        Returns:
            bool: True if a PENDING row exists for this code.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM pending_intents WHERE code=? AND status='PENDING' LIMIT 1",
                (code,),
            ).fetchone()
        return row is not None

    def get_rvol_baseline(self, scan_date: str, code: str) -> float:
        """Return rvol_baseline from daily_scan for the given scan date and code.

        Replaces signal_engine.py rvol read at ~346-351.  Uses a plain scalar
        fetch (no row_factory flip).

        Args:
            scan_date: ISO date string (e.g. "2026-06-24").
            code:      Moomoo-format stock code (e.g. "US.AAPL").

        Returns:
            float: rvol_baseline, or 0.0 if no row exists.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT rvol_baseline FROM daily_scan WHERE scan_date=? AND code=?",
                (scan_date, code),
            ).fetchone()
        return float(row[0]) if row is not None else 0.0

    def get_watchlist_codes(self, scan_date) -> list:
        """Return ordered list of codes from daily_scan for the given scan date.

        Replaces the hasattr-guarded stub in bot.py _job_market_open_subscribe
        (@493) that always returned [].  Reads daily_scan ordered by rank ASC
        so callers receive the canonical scan ordering.

        scan_date: date object or ISO date string.

        Returns:
            list: List of Moomoo-format code strings, ordered by rank ASC.
                  Empty list if no rows exist for that date.
        """
        if hasattr(scan_date, "isoformat"):
            scan_date_str = scan_date.isoformat()
        else:
            scan_date_str = str(scan_date)
        with self._lock:
            rows = self._conn.execute(
                "SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC",
                (scan_date_str,),
            ).fetchall()
        return [r[0] for r in rows]

    # ============================================================
    # Intent / counter mutation methods (CR-01 / T-06.1-09-01)
    # ============================================================

    def increment_daily_filled_count(self, session_date: str, updated_at: str) -> None:
        """Increment the daily filled-trade counter for session_date (guarded write).

        Uses INSERT ... ON CONFLICT(session_date) DO UPDATE so the first fill of
        the day inserts a row with filled_count=1 and subsequent fills increment.

        Replaces manager.py _increment_daily_filled_count body (~965-973).

        Args:
            session_date: ISO date string for the trading session (ET date).
            updated_at:   ISO-8601 timestamp string (ET).
        """
        with self._lock:
            self._conn.execute(
                """INSERT INTO daily_trade_count (session_date, filled_count, updated_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(session_date) DO UPDATE SET
                     filled_count = filled_count + 1,
                     updated_at = excluded.updated_at""",
                (session_date, updated_at),
            )
            self._conn.commit()

    def resolve_pending_intent(self, intent_id: str, resolved_at: str) -> None:
        """Mark a pending_intents row as RESOLVED (guarded write).

        Replaces manager.py _resolve_pending_intent body (~998-1003).

        Args:
            intent_id:   pending_intents.intent_id UUID string.
            resolved_at: ISO-8601 timestamp string (ET fill time).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE pending_intents SET status='RESOLVED', resolved_at=? "
                "WHERE intent_id=? AND status='PENDING'",
                (resolved_at, intent_id),
            )
            self._conn.commit()

    def insert_pending_intent(
        self,
        intent_id: str,
        code: str,
        entry_price: float,
        stop_price: float,
        quantity: int,
        emitted_at: str,
    ) -> None:
        """Insert a new pending_intents row with status='PENDING' (guarded write).

        Replaces risk_engine.py ~161-174.

        Args:
            intent_id:   UUID string for the new intent.
            code:        Moomoo-format stock code (e.g. "US.AAPL").
            entry_price: Limit entry price for the intent.
            stop_price:  Initial stop price for the intent.
            quantity:    Share quantity.
            emitted_at:  ISO-8601 timestamp string (ET) when the intent was emitted.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_intents "
                "(intent_id, code, status, entry_price, stop_price, quantity, emitted_at) "
                "VALUES (?, ?, 'PENDING', ?, ?, ?, ?)",
                (intent_id, code, entry_price, stop_price, quantity, emitted_at),
            )
            self._conn.commit()

    def expire_pending_intent(self, intent_id: str, resolved_at: str) -> None:
        """Mark a pending_intents row as EXPIRED (guarded write).

        Replaces execution/engine.py _resolve_intent_expired body (~462-467).

        Args:
            intent_id:   pending_intents.intent_id UUID string.
            resolved_at: ISO-8601 timestamp string (ET) when expiry was detected.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE pending_intents SET status='EXPIRED', resolved_at=? "
                "WHERE intent_id=?",
                (resolved_at, intent_id),
            )
            self._conn.commit()

    def persist_watchlist(
        self, scan_date, candidates: list, scan_pass: str
    ) -> None:
        """Idempotent upsert of the scan candidate list to daily_scan (guarded write).

        Moves the body of scanner.py _persist_watchlist into the store so the
        lock lives here, not at the external caller (SCAN-05 / CR-01).  The
        per-candidate INSERT...ON CONFLICT loop and final commit are wrapped in
        a single ``with self._lock:`` block.

        Uses INSERT ... ON CONFLICT(scan_date, code) DO UPDATE so re-running
        the same scan day is idempotent (D-05 / SCAN-05).  created_at is
        intentionally excluded from the UPDATE clause — first-seen timestamp
        is preserved across re-scans.

        scan_date:  date object or ISO date string (isoformat() is called if
                    the object exposes it, matching the existing helper convention).
        candidates: List of candidate dicts (code, gap_pct, rank, prior_day_high,
                    prior_close, sma200, rvol_baseline); rank assigned by caller.
        scan_pass:  Label for the originating scan pass (e.g. "premarket").
        """
        from bot.safety.et_helpers import now_et
        created_at = now_et().isoformat()
        scan_date_str = (
            scan_date.isoformat() if hasattr(scan_date, "isoformat") else str(scan_date)
        )
        with self._lock:
            for c in candidates:
                self._conn.execute(
                    """
                    INSERT INTO daily_scan
                        (scan_date, code, gap_pct, rank, created_at,
                         prior_day_high, prior_close, sma200, rvol_baseline, scan_pass)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_date, code) DO UPDATE SET
                        gap_pct        = excluded.gap_pct,
                        rank           = excluded.rank,
                        prior_day_high = excluded.prior_day_high,
                        prior_close    = excluded.prior_close,
                        sma200         = excluded.sma200,
                        rvol_baseline  = excluded.rvol_baseline,
                        scan_pass      = excluded.scan_pass
                    """,
                    (
                        scan_date_str,
                        c["code"],
                        c["gap_pct"],
                        c["rank"],
                        created_at,
                        c.get("prior_day_high"),
                        c.get("prior_close"),
                        c.get("sma200"),
                        c.get("rvol_baseline"),
                        scan_pass,
                    ),
                )
            self._conn.commit()
