#!/usr/bin/env python3
"""
bot.options.store — OptionsStore: persistence for option positions and legs.

Subclass of StateStore (D4/D6): inherits open()/close()/migrations/_lock and the
context-manager protocol, and runs against its OWN database file
(`OptionsStore(cfg.state_db).open()`), so the equity bot's DB is untouched.

Every method follows the StateStore house pattern (IN-01): writes take
`with self._lock:` then commit; reads flip row_factory to sqlite3.Row inside the
same lock and reset it before returning plain dicts. All values are bound with
'?' placeholders — no SQL string interpolation.

The leg option right lives in a column named `right`, quoted as "right" in every
statement (it is a SQLite join keyword).

Exports: OptionsStore
"""
import sqlite3
from typing import Optional

from bot.state.store import StateStore


# Column order matches _migration_0006 (bot/state/migrations.py).
_POSITION_COLUMNS = (
    "position_id", "underlying", "structure", "expiry", "dte_at_entry",
    "ivr_at_entry", "credit_per_spread", "width", "qty", "max_loss_usd",
    "status", "opened_at", "closed_at", "close_reason", "realized_pnl_usd",
)

_LEG_COLUMNS = (
    "leg_id", "position_id", "code", "right", "strike", "side", "qty",
    "entry_order_id", "entry_price", "exit_order_id", "exit_price", "status",
)


def _quote(col: str) -> str:
    """Quote a column name for SQL (only `right` actually needs it)."""
    return f'"{col}"'


class OptionsStore(StateStore):
    """Guarded persistence for option_positions / option_legs / meta (Phase 8)."""

    # --------------------------------------------------------
    # Inserts
    # --------------------------------------------------------

    def insert_option_position(self, pos: dict) -> None:
        """Insert one option_positions row.

        Values are read with pos.get(), so a caller may omit any nullable column
        (it lands as NULL).
        """
        sql = (
            f"INSERT INTO option_positions ({', '.join(_POSITION_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_POSITION_COLUMNS))})"
        )
        with self._lock:
            self._conn.execute(sql, tuple(pos.get(c) for c in _POSITION_COLUMNS))
            self._conn.commit()

    def insert_option_leg(self, leg: dict) -> None:
        """Insert one option_legs row (nullable columns may be omitted)."""
        sql = (
            f"INSERT INTO option_legs ({', '.join(_quote(c) for c in _LEG_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_LEG_COLUMNS))})"
        )
        with self._lock:
            self._conn.execute(sql, tuple(leg.get(c) for c in _LEG_COLUMNS))
            self._conn.commit()

    # --------------------------------------------------------
    # Leg mutation (partial updates)
    # --------------------------------------------------------

    def _update_leg(self, leg_id: str, fields: dict) -> None:
        """UPDATE option_legs with only the non-None fields; no-op when all None."""
        pairs = [(col, val) for col, val in fields.items() if val is not None]
        if not pairs:
            return
        set_clause = ", ".join(f"{col}=?" for col, _ in pairs)
        params = [val for _, val in pairs] + [leg_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE option_legs SET {set_clause} WHERE leg_id=?", params,
            )
            self._conn.commit()

    def set_leg_entry(self, leg_id: str, order_id=None, price=None, status=None) -> None:
        """Record entry progress on a leg — only the arguments supplied are written."""
        self._update_leg(leg_id, {
            "entry_order_id": order_id,
            "entry_price": price,
            "status": status,
        })

    def set_leg_exit(self, leg_id: str, order_id=None, price=None, status=None) -> None:
        """Record exit progress on a leg — only the arguments supplied are written."""
        self._update_leg(leg_id, {
            "exit_order_id": order_id,
            "exit_price": price,
            "status": status,
        })

    # --------------------------------------------------------
    # Position mutation
    # --------------------------------------------------------

    def set_position_status(
        self,
        position_id: str,
        status: str,
        *,
        closed_at=None,
        close_reason=None,
        realized_pnl_usd=None,
    ) -> None:
        """Set a position's status; the close fields are written only when given."""
        cols = ["status"]
        params = [status]
        for col, val in (
            ("closed_at", closed_at),
            ("close_reason", close_reason),
            ("realized_pnl_usd", realized_pnl_usd),
        ):
            if val is not None:
                cols.append(col)
                params.append(val)
        set_clause = ", ".join(f"{c}=?" for c in cols)
        params.append(position_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE option_positions SET {set_clause} WHERE position_id=?", params,
            )
            self._conn.commit()

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    def get_option_positions(self, statuses: tuple) -> list:
        """Return positions in the given statuses, each with its legs attached.

        Args:
            statuses: tuple of status strings. Empty → [] without touching the
                      DB (an empty `IN ()` is a SQL syntax error).

        Returns:
            list[dict]: position rows, each with row["legs"] = list of leg dicts
                        in insertion (rowid) order.
        """
        if not statuses:
            return []
        placeholders = ", ".join("?" * len(statuses))
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                f"SELECT * FROM option_positions WHERE status IN ({placeholders})",
                tuple(statuses),
            ).fetchall()
            positions = [dict(r) for r in rows]
            for pos in positions:
                legs = self._conn.execute(
                    "SELECT * FROM option_legs WHERE position_id=? ORDER BY rowid",
                    (pos["position_id"],),
                ).fetchall()
                pos["legs"] = [dict(r) for r in legs]
            self._conn.row_factory = None
        return positions

    def get_realized_pnl_on(self, date_iso: str) -> float:
        """Sum realized P&L over positions closed on the given ET date (YYYY-MM-DD)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(realized_pnl_usd), 0) FROM option_positions "
                "WHERE closed_at LIKE ? || '%'",
                (date_iso,),
            ).fetchone()
        return float(row[0] or 0.0)

    def count_opened_on(self, date_iso: str) -> int:
        """Count positions opened on the given ET date (per-day entry cap)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM option_positions WHERE opened_at LIKE ? || '%'",
                (date_iso,),
            ).fetchone()
        return int(row[0] or 0)

    # --------------------------------------------------------
    # Generic meta accessors
    # --------------------------------------------------------
    # StateStore only exposes circuit-breaker-specific meta helpers; the options
    # service needs arbitrary keys (e.g. its own daily-loss breaker date).

    def get_meta(self, key: str) -> Optional[str]:
        """Return the meta value for `key`, or None when the key is absent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,),
            ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Upsert a meta key (INSERT ... ON CONFLICT — never raises IntegrityError)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()
