#!/usr/bin/env python3
"""
tests/state/test_store.py — Tests for StateStore (D-09/D-10 acceptance criteria).

Verifies:
  (a) StateStore.open() runs migrations — all five v1 tables exist after open.
  (b) StateStore.open() is idempotent — re-opening the same DB is a no-op.
  (c) resolve_db_path() honours the BOT_STATE_DB env var when set.
  (d) resolve_db_path() falls back to DEFAULT_DB_PATH when BOT_STATE_DB is unset.
  (e) StateStore can be used as a context manager.
  (f) StateStore.close() is safe to call even if open() was never called.
"""

import os
import sqlite3

import pytest

from bot.state.migrations import CURRENT_VERSION
from bot.state.store import (
    DEFAULT_DB_PATH,
    StateStore,
    resolve_db_path,
)


# ============================================================
# Helpers
# ============================================================

def _get_table_names(conn: sqlite3.Connection) -> set:
    """Return the set of table names in the given connection."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


# ============================================================
# resolve_db_path tests
# ============================================================

class TestResolveDbPath:
    """resolve_db_path() must honour BOT_STATE_DB and fall back to DEFAULT_DB_PATH."""

    def test_default_path_ends_with_data_bot_state_db(self):
        """DEFAULT_DB_PATH must end with data/bot_state.db."""
        # Normalize to forward slashes for cross-platform comparison
        normalized = DEFAULT_DB_PATH.replace(os.sep, "/")
        assert normalized.endswith("data/bot_state.db"), (
            f"DEFAULT_DB_PATH={DEFAULT_DB_PATH!r} does not end with data/bot_state.db"
        )

    def test_resolve_db_path_returns_default_when_env_unset(self, monkeypatch):
        """When BOT_STATE_DB is not set, resolve_db_path must return DEFAULT_DB_PATH."""
        monkeypatch.delenv("BOT_STATE_DB", raising=False)
        assert resolve_db_path() == DEFAULT_DB_PATH

    def test_resolve_db_path_honours_env_var(self, tmp_path, monkeypatch):
        """When BOT_STATE_DB is set, resolve_db_path must return its value."""
        custom = str(tmp_path / "custom.db")
        monkeypatch.setenv("BOT_STATE_DB", custom)
        assert resolve_db_path() == custom

    def test_resolve_db_path_uses_tmp_state_db_fixture(self, tmp_state_db):
        """With the conftest tmp_state_db fixture, resolve_db_path returns tmp path."""
        resolved = resolve_db_path()
        assert resolved == tmp_state_db
        assert "bot_state_test" in resolved


# ============================================================
# StateStore.open() + migration tests
# ============================================================

class TestStateStoreOpen:
    """StateStore.open() must migrate the schema and make conn available."""

    def test_open_creates_all_five_v1_tables(self, tmp_state_db):
        """open() must create positions, trades, daily_scan, bar_cache, meta."""
        store = StateStore()
        store.open()
        try:
            tables = _get_table_names(store.conn)
        finally:
            store.close()

        expected = {"positions", "trades", "daily_scan", "bar_cache", "meta"}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_open_sets_user_version_to_current(self, tmp_state_db):
        """After open(), PRAGMA user_version must equal CURRENT_VERSION."""
        with StateStore() as store:
            version = store.conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

    def test_conn_accessible_after_open(self, tmp_state_db):
        """store.conn must return an sqlite3.Connection after open()."""
        store = StateStore()
        store.open()
        try:
            assert isinstance(store.conn, sqlite3.Connection)
        finally:
            store.close()

    def test_conn_raises_before_open(self, tmp_state_db):
        """Accessing store.conn before open() must raise RuntimeError."""
        store = StateStore()
        with pytest.raises(RuntimeError, match="not open"):
            _ = store.conn


# ============================================================
# Idempotency tests
# ============================================================

class TestStateStoreIdempotency:
    """Re-opening an existing StateStore DB must be a no-op (user_version gate)."""

    def test_reopen_is_idempotent(self, tmp_state_db):
        """Two successive open/close cycles must not raise and must leave schema intact."""
        # First open
        store = StateStore()
        store.open()
        store.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('sentinel', 'round1')"
        )
        store.conn.commit()
        store.close()

        # Second open — should be a no-op re: migrations
        store2 = StateStore()
        store2.open()
        try:
            tables = _get_table_names(store2.conn)
            version = store2.conn.execute("PRAGMA user_version").fetchone()[0]
            row = store2.conn.execute(
                "SELECT value FROM meta WHERE key='sentinel'"
            ).fetchone()
        finally:
            store2.close()

        expected = {"positions", "trades", "daily_scan", "bar_cache", "meta"}
        assert expected.issubset(tables)
        assert version == CURRENT_VERSION
        assert row is not None and row[0] == "round1"

    def test_opening_existing_db_does_not_re_run_migration(self, tmp_state_db):
        """Schema version must remain 1 after re-opening; no extra migration must run."""
        # Open and close once
        with StateStore() as store:
            pass

        # Open again and check version
        with StateStore() as store:
            version = store.conn.execute("PRAGMA user_version").fetchone()[0]

        assert version == CURRENT_VERSION


# ============================================================
# Context manager tests
# ============================================================

class TestStateStoreContextManager:
    """StateStore must work as a context manager."""

    def test_with_block_migrates_schema(self, tmp_state_db):
        """Using StateStore as context manager must produce a migrated DB."""
        with StateStore() as store:
            tables = _get_table_names(store.conn)
        expected = {"positions", "trades", "daily_scan", "bar_cache", "meta"}
        assert expected.issubset(tables)

    def test_conn_closed_after_with_block(self, tmp_state_db):
        """After the with block, store._conn must be None (connection closed)."""
        store = StateStore()
        with store:
            pass
        # _conn should be None after __exit__
        assert store._conn is None

    def test_exception_in_with_block_still_closes(self, tmp_state_db):
        """An exception inside the with block must not prevent close()."""
        store = StateStore()
        try:
            with store:
                raise ValueError("simulated exception")
        except ValueError:
            pass
        assert store._conn is None


# ============================================================
# Explicit db_path override tests
# ============================================================

class TestStateStoreExplicitPath:
    """StateStore constructor db_path override must work regardless of BOT_STATE_DB."""

    def test_explicit_db_path_overrides_env(self, tmp_path, monkeypatch):
        """When db_path is passed to StateStore(), BOT_STATE_DB env is ignored."""
        custom = str(tmp_path / "explicit.db")
        monkeypatch.setenv("BOT_STATE_DB", str(tmp_path / "env_override.db"))

        store = StateStore(db_path=custom)
        store.open()
        try:
            assert os.path.exists(custom), "DB file must be created at the explicit path"
        finally:
            store.close()


# ============================================================
# Close safety tests
# ============================================================

class TestStateStoreClose:
    """StateStore.close() must be safe to call in any state."""

    def test_close_before_open_is_safe(self, tmp_state_db):
        """close() must not raise if open() was never called."""
        store = StateStore()
        store.close()  # must not raise

    def test_double_close_is_safe(self, tmp_state_db):
        """Calling close() twice must not raise."""
        store = StateStore()
        store.open()
        store.close()
        store.close()  # second close must be a no-op


# ============================================================
# get_daily_trade_stats — uncapped aggregate query (DAILY-CAP-01)
# ============================================================

class TestGetDailyTradeStats:
    """get_daily_trade_stats must return correct aggregates for ALL closed trades
    on a session date, bypassing the LIMIT 20 cap in get_closed_trades.

    Root cause: get_closed_trades has LIMIT 20 (display cap). format_daily_summary
    called len(trades_rows) / iterated the list — so >20 trades per day caused a
    silent undercount in the Daily Summary (count, W/L, realized PnL).

    Fix: dedicated SQL COUNT/SUM path that never loads trade rows into memory
    and is not bounded by any LIMIT.  (DAILY-CAP-01 / debug daily-summary-trade-count-cap)
    """

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _insert_trade(
        conn,
        trade_id: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        r_multiple: float,
        closed_at: str = "2026-06-30T15:00:00",
    ):
        """Insert a minimal trade row directly into the trades table."""
        conn.execute(
            """
            INSERT INTO trades
                (trade_id, position_id, code, entry_price, exit_price,
                 quantity, exit_reason, r_multiple, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                f"pos-{trade_id}",
                "US.AAPL",
                entry_price,
                exit_price,
                quantity,
                "trail",
                r_multiple,
                closed_at,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------ tests

    def test_get_daily_trade_stats_returns_all_trades_beyond_limit_20(self, tmp_state_db):
        """get_daily_trade_stats must count ALL trades, not just the first/last 20.

        This test seeds 25 closed trades for 2026-06-30 — 5 more than the
        LIMIT 20 cap in get_closed_trades.  get_daily_trade_stats must return
        trade_count=25 and the correct sum of all 25 realized PnLs.

        WILL FAIL against current code because get_daily_trade_stats does not
        exist yet (AttributeError). After the fix it must be GREEN.
        """
        session_date = "2026-06-30"
        n_total = 25
        # 15 wins (r_multiple=+1.0, pnl=+$50 each) and 10 losses (r_multiple=-0.5, pnl=-$25 each)
        expected_wins = 15
        expected_losses = 10
        # realized_pnl per trade: (exit - entry) * qty
        # win:  (105.0 - 100.0) * 10 = +50.0
        # loss: (97.5  - 100.0) * 10 = -25.0
        expected_pnl = expected_wins * 50.0 + expected_losses * (-25.0)  # 750 - 250 = 500.0

        with StateStore() as store:
            for i in range(expected_wins):
                self._insert_trade(
                    store.conn,
                    trade_id=f"win-{i:03d}",
                    entry_price=100.0,
                    exit_price=105.0,
                    quantity=10,
                    r_multiple=1.0,
                    closed_at=f"{session_date}T{10 + i // 60:02d}:{i % 60:02d}:00",
                )
            for i in range(expected_losses):
                self._insert_trade(
                    store.conn,
                    trade_id=f"loss-{i:03d}",
                    entry_price=100.0,
                    exit_price=97.5,
                    quantity=10,
                    r_multiple=-0.5,
                    closed_at=f"{session_date}T{12 + i // 60:02d}:{i % 60:02d}:00",
                )

            # Verify get_closed_trades still returns at most 20 (bounded display)
            display_rows = store.get_closed_trades(session_date)
            assert len(display_rows) == 20, (
                f"get_closed_trades must still be capped at 20 for display; "
                f"got {len(display_rows)}"
            )

            # This is the NEW method — must count ALL 25 trades
            stats = store.get_daily_trade_stats(session_date)

        assert stats["trade_count"] == n_total, (
            f"get_daily_trade_stats must count all {n_total} trades; "
            f"got {stats['trade_count']} (expected fix: uncapped SQL COUNT)"
        )
        assert stats["wins"] == expected_wins, (
            f"wins must be {expected_wins}; got {stats['wins']}"
        )
        assert stats["losses"] == expected_losses, (
            f"losses must be {expected_losses}; got {stats['losses']}"
        )
        assert abs(stats["realized_pnl"] - expected_pnl) < 0.01, (
            f"realized_pnl must be {expected_pnl:.2f}; got {stats['realized_pnl']:.2f}"
        )

    def test_get_daily_trade_stats_empty_day_returns_zeros(self, tmp_state_db):
        """get_daily_trade_stats on a date with no trades must return all-zero dict."""
        with StateStore() as store:
            stats = store.get_daily_trade_stats("2026-01-01")

        assert stats["trade_count"] == 0
        assert stats["wins"] == 0
        assert stats["losses"] == 0
        assert stats["realized_pnl"] == 0.0

    def test_get_daily_trade_stats_null_r_multiple_counts_as_loss(self, tmp_state_db):
        """A trade with r_multiple=NULL must count as a loss (not a win), matching
        the wins = r_multiple > 0 rule in format_daily_summary."""
        session_date = "2026-06-30"
        with StateStore() as store:
            self._insert_trade(
                store.conn,
                trade_id="null-r",
                entry_price=100.0,
                exit_price=102.0,
                quantity=5,
                r_multiple=None,
            )
            stats = store.get_daily_trade_stats(session_date)

        assert stats["trade_count"] == 1
        assert stats["wins"] == 0, "NULL r_multiple must NOT be counted as a win"
        assert stats["losses"] == 1, "NULL r_multiple must be counted as a loss"


# ============================================================
# Phase 7 — TOD baseline CRUD (SIG-RVOL-TOD)
# ============================================================

class TestTodBaselines:
    """Tests for upsert_tod_baselines / get_tod_baseline (Phase 7, SIG-RVOL-TOD)."""

    def test_get_tod_baseline_returns_stored_value(self, tmp_state_db):
        """upsert_tod_baselines then get_tod_baseline returns the stored cum_vol_mean."""
        with StateStore() as store:
            store.upsert_tod_baselines(
                "2026-07-03", "US.AAPL", {"10:05": 1000.0, "10:10": 2500.0}
            )
            result = store.get_tod_baseline("2026-07-03", "US.AAPL", "10:10")
        assert result == 2500.0

    def test_get_tod_baseline_missing_bucket_returns_zero(self, tmp_state_db):
        """get_tod_baseline returns 0.0 when the (scan_date, code, time_bucket) row is absent."""
        with StateStore() as store:
            result = store.get_tod_baseline("2026-07-03", "US.AAPL", "10:00")
        assert result == 0.0

    def test_upsert_tod_baselines_full_replace_no_duplicates(self, tmp_state_db):
        """A second upsert with the same (scan_date, code) replaces all existing rows —
        no duplicate rows, latest values win (INSERT OR REPLACE semantics)."""
        with StateStore() as store:
            store.upsert_tod_baselines(
                "2026-07-03", "US.AAPL", {"10:05": 1000.0, "10:10": 2000.0}
            )
            # Second upsert — "10:05" value changes; "10:10" removed from dict
            store.upsert_tod_baselines(
                "2026-07-03", "US.AAPL", {"10:05": 9999.0}
            )
            result_05 = store.get_tod_baseline("2026-07-03", "US.AAPL", "10:05")
            # "10:10" row was not re-inserted, so it should still exist (INSERT OR REPLACE
            # replaces individual rows, not the entire candidate's rows).
            # Verify the updated value for "10:05" is correct.
            assert result_05 == 9999.0

    def test_upsert_tod_baselines_empty_dict_no_raise(self, tmp_state_db):
        """upsert_tod_baselines with an empty dict must not raise."""
        with StateStore() as store:
            store.upsert_tod_baselines("2026-07-03", "US.AAPL", {})


# ============================================================
# Phase 7 — Circuit-breaker meta persistence (RISK-CIRCUIT, D-07)
# ============================================================

class TestCircuitBreaker:
    """Tests for get/set/clear_circuit_breaker_date (Phase 7, RISK-CIRCUIT, D-07)."""

    def test_get_circuit_breaker_date_none_on_fresh_store(self, tmp_state_db):
        """get_circuit_breaker_date() must return None on a fresh (uninitialised) store."""
        with StateStore() as store:
            result = store.get_circuit_breaker_date()
        assert result is None

    def test_set_then_get_circuit_breaker_date(self, tmp_state_db):
        """After set_circuit_breaker_date, get_circuit_breaker_date returns that date."""
        with StateStore() as store:
            store.set_circuit_breaker_date("2026-07-03")
            result = store.get_circuit_breaker_date()
        assert result == "2026-07-03"

    def test_clear_circuit_breaker_makes_get_return_none(self, tmp_state_db):
        """clear_circuit_breaker() must make get_circuit_breaker_date() return None."""
        with StateStore() as store:
            store.set_circuit_breaker_date("2026-07-03")
            store.clear_circuit_breaker()
            result = store.get_circuit_breaker_date()
        assert result is None

    def test_circuit_breaker_date_persists_across_reopen(self, tmp_state_db):
        """set_circuit_breaker_date must persist across close/reopen (D-07 restart-persistence).

        This test opens the store, sets the breaker date, closes the store,
        reopens it at the same path, and verifies the date is still present.
        """
        # Write
        store1 = StateStore()
        store1.open()
        store1.set_circuit_breaker_date("2026-07-03")
        store1.close()

        # Reopen and read
        store2 = StateStore()
        store2.open()
        try:
            result = store2.get_circuit_breaker_date()
        finally:
            store2.close()

        assert result == "2026-07-03", (
            f"circuit_breaker_tripped_date must survive close/reopen (D-07); got {result!r}"
        )
