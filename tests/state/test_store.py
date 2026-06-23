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
