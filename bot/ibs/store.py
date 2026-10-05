#!/usr/bin/env python3
"""
bot.ibs.store — IbsStore: persistence for IBS positions, trades and orders (IBS-06).

Subclass of StateStore, running against its OWN database file
(`IbsStore(cfg.state_db).open()`, D-13). It subclasses StateStore (rather than
wrapping it) because OpenDWatchdog's reconnect runs the equity startup_reconcile
against the store it is given and needs get_open_positions/has_pending_intent;
those find the (empty) equity tables in this DB.

Every method follows the StateStore house pattern (IN-01): writes take
`with self._lock:` then commit; reads flip row_factory to sqlite3.Row inside the
same lock and reset it before returning plain dicts. All VALUES are bound with
'?' placeholders; only fixed column names / marker counts are interpolated.

Position lifecycle: OPENING -> OPEN -> (CLOSING) -> CLOSED, or ABORTED /
NEEDS_ATTENTION. A partial exit fill keeps the row OPEN and exit_pending=1
(orchestrator ruling 4): the first exit reason and decided date are kept so
trading_days_held / the exit retry survive restarts.

Exports: IbsStore, ACTIVE_STATUSES
"""
import sqlite3
from typing import Optional

from bot.state.store import StateStore


# D-05: statuses covered by the partial unique index ux_ibs_positions_active_code.
ACTIVE_STATUSES = ("OPENING", "OPEN", "CLOSING", "NEEDS_ATTENTION")

# Column order matches _migration_0008 (bot/state/migrations.py).
_POSITION_COLUMNS = (
    "position_id", "code", "qty", "entry_date", "entry_price", "entry_order_id",
    "status", "exit_pending", "exit_reason", "exit_decided_date", "opened_at",
    "closed_at", "close_reason", "realized_pnl_usd",
)

_ORDER_COLUMNS = (
    "order_id", "position_id", "code", "side", "qty", "status",
    "session_date", "created_at",
)


class IbsStore(StateStore):
    """Guarded persistence for ibs_positions / ibs_trades / ibs_orders / meta."""

    # --------------------------------------------------------
    # Positions
    # --------------------------------------------------------

    def insert_position(self, pos: dict) -> None:
        """Insert one ibs_positions row; None columns are omitted (SQL default/NULL).

        sqlite3.IntegrityError propagates when the code already has an active
        row (D-05) — the caller treats that as "already active".
        """
        cols = [c for c in _POSITION_COLUMNS if pos.get(c) is not None]
        sql = (
            f"INSERT INTO ibs_positions ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})"
        )
        with self._lock:
            self._conn.execute(sql, tuple(pos[c] for c in cols))
            self._conn.commit()

    def get_positions(self, statuses: tuple) -> list:
        """Return positions in the given statuses (empty tuple -> [] without a query)."""
        if not statuses:
            return []
        placeholders = ", ".join("?" * len(statuses))
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                f"SELECT * FROM ibs_positions WHERE status IN ({placeholders}) "
                "ORDER BY rowid",
                tuple(statuses),
            ).fetchall()
            out = [dict(r) for r in rows]
            self._conn.row_factory = None
        return out

    def get_active_positions(self) -> list:
        return self.get_positions(ACTIVE_STATUSES)

    def set_position_status(
        self, position_id: str, status: str, *, closed_at=None, close_reason=None,
    ) -> None:
        """Set a position's status; the close fields are written only when given."""
        cols, params = ["status"], [status]
        for col, val in (("closed_at", closed_at), ("close_reason", close_reason)):
            if val is not None:
                cols.append(col)
                params.append(val)
        params.append(position_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE ibs_positions SET {', '.join(c + '=?' for c in cols)} "
                "WHERE position_id=?",
                params,
            )
            self._conn.commit()

    def mark_opened(self, position_id, qty, entry_price, entry_order_id) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE ibs_positions SET qty=?, entry_price=?, entry_order_id=?, "
                "status='OPEN' WHERE position_id=?",
                (qty, entry_price, entry_order_id, position_id),
            )
            self._conn.commit()

    def mark_exit_pending(self, position_id, reason, decided_date) -> None:
        """Flag the exit as decided; keeps the FIRST reason and decided date (ruling 4)."""
        with self._lock:
            self._conn.execute(
                "UPDATE ibs_positions SET exit_pending=1, "
                "exit_reason=COALESCE(exit_reason, ?), "
                "exit_decided_date=COALESCE(exit_decided_date, ?) "
                "WHERE position_id=?",
                (reason, decided_date, position_id),
            )
            self._conn.commit()

    def record_exit_fill(
        self, position_id, trade_id, filled_qty, exit_price, exit_date, closed_at,
    ) -> int:
        """Record one exit fill: trade row + qty/status update in ONE transaction.

        Returns the remaining qty (0 -> the position is CLOSED with
        realized_pnl_usd = sum of its trades). Partial fills keep the row OPEN
        and exit_pending (T-12-14: one ibs_trades row per exit fill).
        """
        filled = int(filled_qty)
        if filled <= 0:
            raise ValueError(f"filled_qty must be > 0, got {filled_qty!r}")
        with self._lock:
            row = self._conn.execute(
                "SELECT qty, entry_price, code, exit_reason FROM ibs_positions "
                "WHERE position_id=?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown position {position_id!r}")
            qty, entry_price, code, reason = row
            pnl = (float(exit_price) - float(entry_price or 0.0)) * filled
            self._conn.execute(
                "INSERT INTO ibs_trades (trade_id, position_id, code, qty, "
                "entry_price, exit_price, exit_date, reason, pnl_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, position_id, code, filled, entry_price,
                 exit_price, exit_date, reason, pnl),
            )
            remaining = max(int(qty) - filled, 0)
            if remaining > 0:
                self._conn.execute(
                    "UPDATE ibs_positions SET qty=?, status='OPEN' WHERE position_id=?",
                    (remaining, position_id),
                )
            else:
                self._conn.execute(
                    "UPDATE ibs_positions SET qty=0, status='CLOSED', exit_pending=0, "
                    "closed_at=?, close_reason=?, realized_pnl_usd=("
                    "SELECT COALESCE(SUM(pnl_usd), 0) FROM ibs_trades WHERE position_id=?) "
                    "WHERE position_id=?",
                    (closed_at, reason, position_id, position_id),
                )
            self._conn.commit()
        return remaining

    # --------------------------------------------------------
    # Orders
    # --------------------------------------------------------

    def insert_order(self, order: dict) -> None:
        """Upsert an ibs_orders row (re-insert only refreshes status)."""
        with self._lock:
            self._conn.execute(
                f"INSERT INTO ibs_orders ({', '.join(_ORDER_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_ORDER_COLUMNS))}) "
                "ON CONFLICT(order_id) DO UPDATE SET status=excluded.status",
                tuple(order.get(c) for c in _ORDER_COLUMNS),
            )
            self._conn.commit()

    def set_order_status(self, order_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE ibs_orders SET status=? WHERE order_id=?", (status, order_id),
            )
            self._conn.commit()

    def close_working_orders(self, position_id: str, status: str = "DONE") -> list:
        """Move a position's WORKING orders to `status`; return their order ids."""
        with self._lock:
            ids = [r[0] for r in self._conn.execute(
                "SELECT order_id FROM ibs_orders "
                "WHERE position_id=? AND status='WORKING' ORDER BY rowid",
                (position_id,),
            ).fetchall()]
            self._conn.execute(
                "UPDATE ibs_orders SET status=? WHERE position_id=? AND status='WORKING'",
                (status, position_id),
            )
            self._conn.commit()
        return ids

    def get_orders(self, statuses: tuple) -> list:
        if not statuses:
            return []
        placeholders = ", ".join("?" * len(statuses))
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                f"SELECT * FROM ibs_orders WHERE status IN ({placeholders}) ORDER BY rowid",
                tuple(statuses),
            ).fetchall()
            out = [dict(r) for r in rows]
            self._conn.row_factory = None
        return out

    # --------------------------------------------------------
    # Trades
    # --------------------------------------------------------

    def get_trades_on(self, date_iso: str) -> list:
        """Exit trades whose exit_date is the given ET date (YYYY-MM-DD)."""
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM ibs_trades WHERE exit_date=? ORDER BY rowid", (date_iso,),
            ).fetchall()
            out = [dict(r) for r in rows]
            self._conn.row_factory = None
        return out

    def get_realized_pnl_on(self, date_iso: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM ibs_trades WHERE exit_date=?",
                (date_iso,),
            ).fetchone()
        return float(row[0] or 0.0)

    # --------------------------------------------------------
    # Generic meta accessors (same as OptionsStore)
    # --------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,),
            ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()
