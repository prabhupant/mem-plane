"""Async SQLite connection management with sqlite-vec extension support."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite
import sqlite_vec

# Prefer sqlean when standard sqlite3 lacks load_extension (common on macOS)
_sqlite_module: Any
try:
    import sqlite3

    _dummy_conn = sqlite3.connect(":memory:")
    if hasattr(_dummy_conn, "enable_load_extension"):
        _sqlite_module = sqlite3
    else:
        import sqlean

        _sqlite_module = sqlean
    _dummy_conn.close()
except (ImportError, AttributeError, sqlite3.Error):
    import sqlean

    _sqlite_module = sqlean


def get_sqlite_connector(database: str | Path) -> Callable[[], Any]:
    """Create a synchronous connector function that enables extensions and loads sqlite-vec."""
    db_path = str(database)

    def connector() -> Any:
        conn = _sqlite_module.connect(db_path)
        if hasattr(conn, "enable_load_extension"):
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        else:
            sqlite_vec.load(conn)
        return conn

    return connector


async def create_connection(database: str | Path = ":memory:") -> aiosqlite.Connection:
    """Create and initialize an aiosqlite connection with sqlite-vec and performance PRAGMAs."""
    connector = get_sqlite_connector(database)
    conn = aiosqlite.Connection(connector, iter_chunk_size=64)
    await conn

    # Apply performance pragmas
    if str(database) != ":memory:":
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")

    return conn
