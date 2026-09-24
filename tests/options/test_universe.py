#!/usr/bin/env python3
"""
tests/options/test_universe.py — read-only equity watchlist reader (MSO-06, D-17).

Builds a real equity-schema DB via bot.state.migrations.run_migrations against a
tmp_path SQLite file so tests exercise the actual daily_scan table shape. Only
bot/options/universe.py (production code) is barred from importing bot.state —
these tests are free to build the real schema with it.
"""
import ast
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.state.migrations import run_migrations
from bot.options.universe import read_equity_watchlist, _connect_ro


# ============================================================
# Fixtures / helpers
# ============================================================

@pytest.fixture
def equity_db(tmp_path):
    """A real equity-schema SQLite DB (WAL mode) at tmp_path/bot_state.db."""
    path = tmp_path / "bot_state.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    run_migrations(conn)
    yield path, conn
    conn.close()


def _seed(conn, scan_date, code_ranks):
    """Insert (scan_date, code, gap_pct=3.0, rank, created_at) rows and commit."""
    for code, rank in code_ranks:
        conn.execute(
            "INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at) "
            "VALUES (?, ?, 3.0, ?, '2026-09-17T12:00:00Z')",
            (scan_date, code, rank),
        )
    conn.commit()


@pytest.fixture(autouse=True)
def mock_logger(monkeypatch):
    """Replace bot.options.universe._logger with a MagicMock for assertion."""
    mock = MagicMock()
    monkeypatch.setattr("bot.options.universe._logger", mock)
    return mock


# ============================================================
# Happy path
# ============================================================

def test_reads_todays_codes_in_rank_order(equity_db):
    path, conn = equity_db
    _seed(conn, "2026-09-17", [("US.CCC", 3), ("US.AAA", 1), ("US.BBB", 2)])

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == ["US.AAA", "US.BBB", "US.CCC"]


def test_caps_at_twenty_by_rank(equity_db):
    path, conn = equity_db
    ranks = list(range(1, 24))
    import random
    shuffled = ranks[:]
    random.Random(7).shuffle(shuffled)
    _seed(conn, "2026-09-17", [(f"US.SYM{r:02d}", r) for r in shuffled])

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert len(codes) == 20
    assert codes == [f"US.SYM{r:02d}" for r in range(1, 21)]


def test_other_scan_dates_excluded(equity_db):
    path, conn = equity_db
    _seed(conn, "2026-09-16", [("US.OLD", 1)])
    _seed(conn, "2026-09-17", [("US.NEW", 1)])

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == ["US.NEW"]


# ============================================================
# Fail-closed paths
# ============================================================

def test_empty_day_returns_empty_and_warns(equity_db, mock_logger):
    path, conn = equity_db

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == []
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args[0][0] == "options_watchlist_empty"


def test_missing_file_returns_empty_and_creates_nothing(tmp_path, mock_logger):
    path = tmp_path / "does_not_exist.db"

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == []
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args[0][0] == "options_watchlist_unavailable"
    assert not path.exists()


def test_locked_db_returns_empty(tmp_path, mock_logger):
    path = tmp_path / "locked.db"
    writer = sqlite3.connect(str(path))
    writer.execute("PRAGMA journal_mode=DELETE")
    writer.execute(
        "CREATE TABLE daily_scan (scan_date TEXT, code TEXT, gap_pct REAL, "
        "rank INTEGER, created_at TEXT)"
    )
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO daily_scan VALUES ('2026-09-17', 'US.X', 3.0, 1, 'now')")
    try:
        codes = read_equity_watchlist(str(path), "2026-09-17", timeout_s=0.1)
        assert codes == []
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "options_watchlist_unavailable"
    finally:
        writer.rollback()
        writer.close()


def test_corrupt_file_returns_empty(tmp_path, mock_logger):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == []
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args[0][0] == "options_watchlist_unavailable"


def test_missing_table_returns_empty(tmp_path, mock_logger):
    path = tmp_path / "no_table.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE meta (key TEXT)")
    conn.commit()
    conn.close()

    codes = read_equity_watchlist(str(path), "2026-09-17")

    assert codes == []
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args[0][0] == "options_watchlist_unavailable"


# ============================================================
# WAL concurrency + read-only enforcement
# ============================================================

def test_wal_reader_sees_committed_rows_during_open_write(equity_db):
    path, conn = equity_db
    _seed(conn, "2026-09-17", [("US.COMMITTED", 1)])

    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at) "
        "VALUES ('2026-09-17', 'US.UNCOMMITTED', 3.0, 2, 'now')"
    )
    try:
        codes = read_equity_watchlist(str(path), "2026-09-17")
        assert codes == ["US.COMMITTED"]
    finally:
        conn.rollback()


def test_connection_is_read_only(equity_db):
    path, conn = equity_db
    _seed(conn, "2026-09-17", [("US.X", 1)])

    ro_conn = _connect_ro(str(path), 1.0)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute(
                "INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at) "
                "VALUES ('2026-09-18', 'US.Y', 3.0, 1, 'now')"
            )
    finally:
        ro_conn.close()

    count = conn.execute("SELECT COUNT(*) FROM daily_scan").fetchone()[0]
    assert count == 1


# ============================================================
# Static isolation guarantee (D-17)
# ============================================================

def test_module_does_not_import_equity_state():
    source = Path("bot/options/universe.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("bot.state"), (
                f"universe.py must not import from {node.module}"
            )
            for alias in node.names:
                assert alias.name != "StateStore"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("bot.state")
