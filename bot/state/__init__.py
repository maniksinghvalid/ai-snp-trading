#!/usr/bin/env python3
"""
bot/state — SQLite persistence layer for the AI S&P Trading Bot.

Provides StateStore (durable SQLite with versioned migration runner) and
atomic_write_json (temp-file + os.replace snapshot writer).

Re-exports are populated after bot/state/store.py is created (Task 2).
"""
