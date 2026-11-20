"""Tests for SQLite connection, pragmas, and filesystem permissions."""

from __future__ import annotations

import os
from pathlib import Path

from mem_plane.storage.sqlite.connection import (
    create_connection,
    create_sync_connection,
    prepare_db_file,
)


def test_prepare_db_file_permissions(tmp_path: Path) -> None:
    db_file = tmp_path / "subdir" / "test.db"
    prepared = prepare_db_file(db_file)

    assert os.path.exists(prepared)
    dir_mode = os.stat(db_file.parent).st_mode & 0o777
    file_mode = os.stat(prepared).st_mode & 0o777

    assert dir_mode == 0o700
    assert file_mode == 0o600


def test_sync_connection_pragmas(tmp_path: Path) -> None:
    db_file = tmp_path / "pragmas.db"
    conn = create_sync_connection(db_file)
    try:
        cur = conn.execute("PRAGMA busy_timeout;")
        timeout = cur.fetchone()[0]
        assert timeout == 5000

        cur = conn.execute("PRAGMA foreign_keys;")
        fk = cur.fetchone()[0]
        assert fk == 1

        cur = conn.execute("PRAGMA journal_mode;")
        journal = cur.fetchone()[0].lower()
        assert journal == "wal"

        cur = conn.execute("SELECT vec_version();")
        vec_ver = cur.fetchone()[0]
        assert vec_ver is not None
    finally:
        conn.close()


async def test_async_connection_pragmas(tmp_path: Path) -> None:
    db_file = tmp_path / "async_pragmas.db"
    conn = await create_connection(db_file)
    try:
        cur = await conn.execute("PRAGMA busy_timeout;")
        row = await cur.fetchone()
        assert row is not None and row[0] == 5000

        cur = await conn.execute("PRAGMA foreign_keys;")
        row = await cur.fetchone()
        assert row is not None and row[0] == 1

        cur = await conn.execute("PRAGMA journal_mode;")
        row = await cur.fetchone()
        assert row is not None and row[0].lower() == "wal"
    finally:
        await conn.close()
