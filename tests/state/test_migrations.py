#!/usr/bin/env python3
"""
tests/state/test_migrations.py — Tests for bot/state/migrations.py

Verifies:
  (a) Fresh DB: run_migrations creates all five v1 tables and sets user_version = 1.
  (b) Re-run idempotency: calling run_migrations a second time is a no-op
      (user_version stays 1, no error, tables intact).
  (c) UNIQUE constraints: daily_scan(scan_date, code) and bar_cache(code, time_key)
      reject duplicate inserts with sqlite3.IntegrityError.
"""

import sqlite3

import pytest

from bot.state.migrations import run_migrations, CURRENT_VERSION, MIGRATIONS


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def in_memory_conn():
    """Return a fresh in-memory SQLite connection, closed after the test."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


# ============================================================
# Core schema creation tests
# ============================================================

class TestMigrationFreshDb:
    """Migration 0001 applied to an empty database."""

    def test_all_five_tables_created(self, in_memory_conn):
        """After run_migrations, all v1 tables must exist in sqlite_master."""
        run_migrations(in_memory_conn)
        rows = in_memory_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        actual_names = {r[0] for r in rows}
        expected = {"positions", "trades", "daily_scan", "bar_cache", "meta"}
        assert expected.issubset(actual_names), (
            f"Missing tables: {expected - actual_names}"
        )

    def test_user_version_equals_current_version(self, in_memory_conn):
        """PRAGMA user_version must equal CURRENT_VERSION (1) after migration."""
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION == 1

    def test_positions_columns_present(self, in_memory_conn):
        """positions table must have all required columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(positions)").fetchall()
        col_names = {row[1] for row in info}
        required = {
            "position_id", "code", "phase", "entry_price", "initial_stop",
            "trail_stop", "full_quantity", "remaining_quantity",
            "opened_at", "updated_at",
        }
        assert required.issubset(col_names), (
            f"Missing positions columns: {required - col_names}"
        )

    def test_trades_columns_present(self, in_memory_conn):
        """trades table must have all required columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(trades)").fetchall()
        col_names = {row[1] for row in info}
        required = {
            "trade_id", "position_id", "code", "entry_price", "exit_price",
            "quantity", "exit_reason", "r_multiple", "closed_at",
        }
        assert required.issubset(col_names), (
            f"Missing trades columns: {required - col_names}"
        )

    def test_daily_scan_columns_present(self, in_memory_conn):
        """daily_scan table must have all required columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names = {row[1] for row in info}
        required = {"scan_date", "code", "gap_pct", "rank", "created_at"}
        assert required.issubset(col_names), (
            f"Missing daily_scan columns: {required - col_names}"
        )

    def test_bar_cache_columns_present(self, in_memory_conn):
        """bar_cache table must have all required columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(bar_cache)").fetchall()
        col_names = {row[1] for row in info}
        required = {"code", "time_key", "open", "high", "low", "close", "volume"}
        assert required.issubset(col_names), (
            f"Missing bar_cache columns: {required - col_names}"
        )

    def test_meta_columns_present(self, in_memory_conn):
        """meta table must have key and value columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(meta)").fetchall()
        col_names = {row[1] for row in info}
        assert {"key", "value"}.issubset(col_names)


# ============================================================
# Idempotency tests
# ============================================================

class TestMigrationIdempotency:
    """Calling run_migrations twice on the same DB must be a no-op."""

    def test_second_run_does_not_raise(self, in_memory_conn):
        """run_migrations must not raise on an already-migrated DB."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)  # second call — must be silent

    def test_user_version_stable_after_second_run(self, in_memory_conn):
        """user_version must remain 1 after the second run_migrations call."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

    def test_tables_intact_after_second_run(self, in_memory_conn):
        """All five tables must still be present after the second run."""
        run_migrations(in_memory_conn)
        # Insert a sentinel row into meta before the second run
        in_memory_conn.execute(
            "INSERT INTO meta (key, value) VALUES ('sentinel', 'alive')"
        )
        in_memory_conn.commit()

        run_migrations(in_memory_conn)  # idempotent call

        # Sentinel row must be preserved (no DROP + re-CREATE)
        row = in_memory_conn.execute(
            "SELECT value FROM meta WHERE key='sentinel'"
        ).fetchone()
        assert row is not None and row[0] == "alive"


# ============================================================
# UNIQUE constraint tests
# ============================================================

class TestUniqueConstraints:
    """UNIQUE constraints on daily_scan and bar_cache reject duplicates."""

    def test_daily_scan_duplicate_raises(self, in_memory_conn):
        """Inserting (scan_date, code) twice must raise sqlite3.IntegrityError."""
        run_migrations(in_memory_conn)
        sql = """
            INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at)
            VALUES (?, ?, ?, ?, ?)
        """
        in_memory_conn.execute(sql, ("2026-06-23", "US.AAPL", 4.5, 1, "2026-06-23T09:00:00Z"))
        in_memory_conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            in_memory_conn.execute(
                sql, ("2026-06-23", "US.AAPL", 5.0, 2, "2026-06-23T09:01:00Z")
            )

    def test_daily_scan_different_dates_allowed(self, in_memory_conn):
        """Same code on different scan dates must succeed (no UNIQUE violation)."""
        run_migrations(in_memory_conn)
        sql = """
            INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at)
            VALUES (?, ?, ?, ?, ?)
        """
        in_memory_conn.execute(sql, ("2026-06-23", "US.AAPL", 4.5, 1, "2026-06-23T09:00:00Z"))
        in_memory_conn.execute(sql, ("2026-06-24", "US.AAPL", 3.2, 1, "2026-06-24T09:00:00Z"))
        in_memory_conn.commit()  # must not raise

    def test_bar_cache_duplicate_raises(self, in_memory_conn):
        """Inserting (code, time_key) twice must raise sqlite3.IntegrityError."""
        run_migrations(in_memory_conn)
        sql = """
            INSERT INTO bar_cache (code, time_key, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        in_memory_conn.execute(
            sql, ("US.AAPL", "2026-06-23T10:05:00", 185.0, 186.0, 184.5, 185.5, 100000)
        )
        in_memory_conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            in_memory_conn.execute(
                sql, ("US.AAPL", "2026-06-23T10:05:00", 185.1, 186.1, 184.6, 185.6, 110000)
            )

    def test_bar_cache_different_time_keys_allowed(self, in_memory_conn):
        """Same code on different time_key values must succeed."""
        run_migrations(in_memory_conn)
        sql = """
            INSERT INTO bar_cache (code, time_key, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        in_memory_conn.execute(
            sql, ("US.AAPL", "2026-06-23T10:05:00", 185.0, 186.0, 184.5, 185.5, 100000)
        )
        in_memory_conn.execute(
            sql, ("US.AAPL", "2026-06-23T10:10:00", 185.5, 187.0, 185.0, 186.5, 120000)
        )
        in_memory_conn.commit()  # must not raise


# ============================================================
# Module-level sanity tests
# ============================================================

class TestMigrationModuleConstants:
    """Sanity-check on MIGRATIONS list and CURRENT_VERSION."""

    def test_migrations_is_non_empty_list(self):
        assert isinstance(MIGRATIONS, list) and len(MIGRATIONS) >= 1

    def test_current_version_matches_migrations_length(self):
        assert CURRENT_VERSION == len(MIGRATIONS)

    def test_migration_0001_contains_all_expected_keywords(self):
        """Migration 0001 SQL must reference all five table names."""
        sql = MIGRATIONS[0]
        for table in ("positions", "trades", "daily_scan", "bar_cache", "meta"):
            assert table in sql, f"Migration 0001 SQL missing table: {table}"

    def test_migration_sql_contains_pragma_user_version_in_runner(self, in_memory_conn):
        """After run_migrations, PRAGMA user_version reflects version (1)."""
        run_migrations(in_memory_conn)
        v = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 1
