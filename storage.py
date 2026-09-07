"""
Shared SQLite connection. Any cog can import get_connection() and create
its own table(s) — one database file, but each cog owns its own schema,
so cogs never collide with each other's data.
"""

import os
import sqlite3

_VOLUME_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_FILE = os.path.join(_VOLUME_DIR, "bot.db")

_connection = None


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(DB_FILE, check_same_thread=False)
        # WAL mode = readers don't block writers, better for a bot doing
        # frequent small writes.
        _connection.execute("PRAGMA journal_mode=WAL;")
    return _connection