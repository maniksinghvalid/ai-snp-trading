#!/usr/bin/env python3
"""
bot/state — SQLite persistence layer for the AI S&P Trading Bot.

Provides StateStore (durable SQLite with versioned migration runner) and
atomic_write_json (temp-file + os.replace snapshot writer).
"""

from bot.state.store import StateStore, atomic_write_json, resolve_db_path, DEFAULT_DB_PATH

__all__ = [
    "StateStore",
    "atomic_write_json",
    "resolve_db_path",
    "DEFAULT_DB_PATH",
]
