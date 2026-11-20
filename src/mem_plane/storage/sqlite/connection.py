"""SQLite connection management with sqlite-vec extension and permission security."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite
import sqlite_vec

DEFAULT_DB_PATH = Path("~/.mem-plane/memory.db")

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


def prepare_db_file(database: str | Path) -> str:
    """Ensure directory permissions are 0700 and db file permissions are 0600."""
    db_str = str(database)
    if db_str == ":memory:":
        return db_str

    db_path = Path(db_str).expanduser()
    parent = db_path.parent
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass

    if not db_path.exists():
        fd = os.open(str(db_path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    else:
        try:
            os.chmod(str(db_path), 0o600)
        except OSError:
            pass

    return str(db_path)


def get_sqlite_connector(database: str | Path | None = None) -> Callable[[], Any]:
    """Create a synchronous connector function that enables extensions and loads sqlite-vec."""
    target = (
        database
        if database is not None
        else os.environ.get("MEM_PLANE_DB_PATH", str(DEFAULT_DB_PATH))
    )
    db_path = prepare_db_file(target)

    def connector() -> Any:
        conn = _sqlite_module.connect(db_path, check_same_thread=False)
        if hasattr(conn, "enable_load_extension"):
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        else:
            sqlite_vec.load(conn)
        return conn

    return connector


def create_sync_connection(database: str | Path | None = None) -> Any:
    """Create and initialize a synchronous SQLite connection with pragmas."""
    target = (
        database
        if database is not None
        else os.environ.get("MEM_PLANE_DB_PATH", str(DEFAULT_DB_PATH))
    )
    db_path = prepare_db_file(target)
    connector = get_sqlite_connector(db_path)
    conn = connector()

    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

    return conn


async def create_connection(database: str | Path | None = None) -> aiosqlite.Connection:
    """Create and initialize an aiosqlite connection with sqlite-vec and performance PRAGMAs."""
    target = (
        database
        if database is not None
        else os.environ.get("MEM_PLANE_DB_PATH", str(DEFAULT_DB_PATH))
    )
    db_path = prepare_db_file(target)
    connector = get_sqlite_connector(db_path)
    conn = aiosqlite.Connection(connector, iter_chunk_size=64)
    await conn

    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    if str(db_path) != ":memory:":
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

    return conn
