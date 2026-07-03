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
        """PRAGMA user_version must equal CURRENT_VERSION after all migrations."""
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

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
        """user_version must remain at CURRENT_VERSION after the second run_migrations call."""
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
        """After run_migrations, PRAGMA user_version reflects CURRENT_VERSION."""
        run_migrations(in_memory_conn)
        v = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == CURRENT_VERSION


# ============================================================
# Migration 0002 tests (D-08 rich-context columns)
# ============================================================

class TestMigration0002FreshDb:
    """Migration 0002 columns added to daily_scan on a fresh DB."""

    def test_migration_0002_adds_columns(self, in_memory_conn):
        """After run_migrations, daily_scan has all five new Phase 2 columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names = {row[1] for row in info}
        expected_new = {"prior_day_high", "prior_close", "sma200", "rvol_baseline", "scan_pass"}
        assert expected_new.issubset(col_names), (
            f"Missing new columns: {expected_new - col_names}"
        )

    def test_user_version_is_2_after_migration(self, in_memory_conn):
        """PRAGMA user_version must equal CURRENT_VERSION (3) after all migrations.

        Note: originally tested for version==2; updated to CURRENT_VERSION after
        migration 0003 was added in Phase 3. The test name is preserved for git
        history continuity; CURRENT_VERSION now equals 3.
        """
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION


class TestMigration0002Idempotency:
    """Running run_migrations twice on a v2 DB must be a no-op."""

    def test_migration_idempotent_v2(self, in_memory_conn):
        """Calling run_migrations twice raises no error and stays at CURRENT_VERSION.

        Note: class name retained for history; now CURRENT_VERSION == 3 after
        migration 0003 was added in Phase 3.
        """
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)  # second call — must be a no-op
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

    def test_v2_tables_intact_after_second_run(self, in_memory_conn):
        """After second run_migrations call, all new columns still present."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names = {row[1] for row in info}
        assert "scan_pass" in col_names


class TestUpgradeFromV1Db:
    """A DB already at user_version=1 gains the 0002 columns on next run_migrations."""

    def test_upgrade_from_v1_db(self, in_memory_conn):
        """A DB at v1 (0001 only) must gain the five new columns when run_migrations runs."""
        # Simulate a v1 database by only applying migration 0001 directly
        in_memory_conn.executescript(MIGRATIONS[0])
        in_memory_conn.execute("PRAGMA user_version = 1")
        in_memory_conn.commit()

        # Verify v1 columns are present and new ones absent
        info_before = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names_before = {row[1] for row in info_before}
        assert "scan_pass" not in col_names_before, "scan_pass should not exist in v1 schema"

        # Apply outstanding migrations (should apply 0002 only)
        run_migrations(in_memory_conn)

        info_after = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names_after = {row[1] for row in info_after}
        expected_new = {"prior_day_high", "prior_close", "sma200", "rvol_baseline", "scan_pass"}
        assert expected_new.issubset(col_names_after), (
            f"Missing columns after v1→v2 upgrade: {expected_new - col_names_after}"
        )

        # user_version must now be CURRENT_VERSION (3 after Phase 3 migration 0003)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION


class TestMigration0002PartialApplication:
    """WR-03: migration 0002 must be idempotent at the column level so a partial
    application (some columns committed, user_version still 1) does not wedge
    future runs with a 'duplicate column name' error."""

    def test_reapply_after_partial_columns_does_not_raise(self, in_memory_conn):
        """Simulate a crash mid-0002: 0001 applied, user_version=1, and SOME of the
        0002 columns already added. run_migrations must complete without error and
        end at v2 with all columns present.
        """
        # Apply 0001 and mark v1.
        in_memory_conn.executescript(MIGRATIONS[0])
        in_memory_conn.execute("PRAGMA user_version = 1")
        # Pre-add two of the five 0002 columns (a partially-applied 0002 script).
        in_memory_conn.execute("ALTER TABLE daily_scan ADD COLUMN prior_day_high REAL")
        in_memory_conn.execute("ALTER TABLE daily_scan ADD COLUMN prior_close REAL")
        in_memory_conn.commit()

        # Re-running migrations must NOT raise duplicate-column on the pre-added cols.
        run_migrations(in_memory_conn)

        info = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_names = {row[1] for row in info}
        expected_new = {"prior_day_high", "prior_close", "sma200", "rvol_baseline", "scan_pass"}
        assert expected_new.issubset(col_names), (
            f"Missing columns after partial-then-full apply: {expected_new - col_names}"
        )
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION  # 3 after Phase 3 migration 0003 added

    def test_no_duplicate_columns_after_reapply(self, in_memory_conn):
        """Each 0002 column must appear exactly once after a partial-then-full apply."""
        in_memory_conn.executescript(MIGRATIONS[0])
        in_memory_conn.execute("PRAGMA user_version = 1")
        in_memory_conn.execute("ALTER TABLE daily_scan ADD COLUMN sma200 REAL")
        in_memory_conn.commit()

        run_migrations(in_memory_conn)

        info = in_memory_conn.execute("PRAGMA table_info(daily_scan)").fetchall()
        col_list = [row[1] for row in info]
        assert col_list.count("sma200") == 1, "sma200 must not be duplicated on re-apply"


# ============================================================
# Migration 0003 tests (D-08/D-12 daily counter + pending intents)
# ============================================================

class TestMigration0003FreshDb:
    """Migration 0003 adds daily_trade_count and pending_intents tables."""

    def test_migration_0003_creates_both_tables(self, in_memory_conn):
        """After run_migrations, daily_trade_count and pending_intents tables exist."""
        run_migrations(in_memory_conn)
        rows = in_memory_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        actual_names = {r[0] for r in rows}
        assert "daily_trade_count" in actual_names, (
            "daily_trade_count table missing after migration 0003"
        )
        assert "pending_intents" in actual_names, (
            "pending_intents table missing after migration 0003"
        )

    def test_migration_0003_user_version_is_3(self, in_memory_conn):
        """PRAGMA user_version must equal CURRENT_VERSION after all migrations.

        Note: originally tested for version==3; updated to CURRENT_VERSION after
        migration 0004 was added in Phase 4. The test name is preserved for git
        history continuity; CURRENT_VERSION now equals 4.
        """
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

    def test_migration_0003_daily_trade_count_columns(self, in_memory_conn):
        """daily_trade_count must have session_date, filled_count, updated_at columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(daily_trade_count)").fetchall()
        col_names = {row[1] for row in info}
        required_dtc = {"session_date", "filled_count", "updated_at"}
        assert required_dtc.issubset(col_names), (
            f"Missing daily_trade_count columns: {required_dtc - col_names}"
        )

    def test_migration_0003_pending_intents_columns(self, in_memory_conn):
        """pending_intents must have all required columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(pending_intents)").fetchall()
        col_names = {row[1] for row in info}
        required = {
            "intent_id", "code", "status", "entry_price",
            "stop_price", "quantity", "emitted_at", "resolved_at",
        }
        assert required.issubset(col_names), (
            f"Missing pending_intents columns: {required - col_names}"
        )


class TestMigration0003Idempotency:
    """Running run_migrations twice on a v3 DB must be a no-op."""

    def test_migration_0003_idempotent(self, in_memory_conn):
        """Calling run_migrations twice raises no error and stays at CURRENT_VERSION.

        Note: class name retained for history; now CURRENT_VERSION == 4 after
        migration 0004 was added in Phase 4.
        """
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)  # second call — must be a no-op
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_VERSION

    def test_v3_tables_intact_after_second_run(self, in_memory_conn):
        """After second run_migrations call, both new tables still present."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)
        rows = in_memory_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "daily_trade_count" in names
        assert "pending_intents" in names


# ============================================================
# Migration 0005 tests (Phase 7: tod_baselines + broker_stop_order_id)
# ============================================================

class TestMigration0005FreshDb:
    """Migration 0005 adds the tod_baselines table and broker_stop_order_id column."""

    def test_migration_0005_user_version_is_5(self, in_memory_conn):
        """PRAGMA user_version must equal 5 after all migrations (migration 0005 added)."""
        run_migrations(in_memory_conn)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 5

    def test_migration_0005_tod_baselines_table_exists(self, in_memory_conn):
        """After run_migrations, sqlite_master must contain a table named tod_baselines."""
        run_migrations(in_memory_conn)
        rows = in_memory_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "tod_baselines" in names, "tod_baselines table missing after migration 0005"

    def test_migration_0005_tod_baselines_columns(self, in_memory_conn):
        """tod_baselines must have scan_date, code, time_bucket, cum_vol_mean columns."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(tod_baselines)").fetchall()
        col_names = {row[1] for row in info}
        required = {"scan_date", "code", "time_bucket", "cum_vol_mean"}
        assert required.issubset(col_names), (
            f"Missing tod_baselines columns: {required - col_names}"
        )

    def test_migration_0005_tod_baselines_composite_pk(self, in_memory_conn):
        """tod_baselines must enforce the composite PRIMARY KEY (scan_date, code, time_bucket).

        Attempting to insert a duplicate (scan_date, code, time_bucket) triple must raise
        sqlite3.IntegrityError.
        """
        run_migrations(in_memory_conn)
        in_memory_conn.execute(
            "INSERT INTO tod_baselines (scan_date, code, time_bucket, cum_vol_mean) "
            "VALUES ('2026-07-03', 'US.AAPL', '10:05', 50000.0)"
        )
        in_memory_conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            in_memory_conn.execute(
                "INSERT INTO tod_baselines (scan_date, code, time_bucket, cum_vol_mean) "
                "VALUES ('2026-07-03', 'US.AAPL', '10:05', 75000.0)"
            )

    def test_migration_0005_broker_stop_order_id_on_positions(self, in_memory_conn):
        """PRAGMA table_info(positions) must include a broker_stop_order_id column (TEXT)."""
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(positions)").fetchall()
        col_names = {row[1] for row in info}
        assert "broker_stop_order_id" in col_names, (
            "broker_stop_order_id column missing from positions after migration 0005"
        )


class TestMigration0005Idempotency:
    """Running run_migrations twice after migration 0005 must be a no-op (WR-03)."""

    def test_migration_0005_idempotent_run_twice_no_raise(self, in_memory_conn):
        """run_migrations twice must not raise and must leave user_version at 5."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)  # second call — idempotent (WR-03)
        version = in_memory_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 5

    def test_migration_0005_idempotent_broker_stop_order_id_not_duplicated(self, in_memory_conn):
        """broker_stop_order_id must appear exactly once after two run_migrations calls."""
        run_migrations(in_memory_conn)
        run_migrations(in_memory_conn)
        info = in_memory_conn.execute("PRAGMA table_info(positions)").fetchall()
        col_list = [row[1] for row in info]
        assert col_list.count("broker_stop_order_id") == 1, (
            "broker_stop_order_id must not be duplicated on re-apply"
        )
