"""Tests for schema initialization, sqlite-vec compatibility, and metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

from mem_plane.storage.sqlite.connection import create_sync_connection
from mem_plane.storage.sqlite.schema import (
    check_meta_compatibility,
    init_schema,
    supports_vec_partition_keys,
)


def test_schema_init_fresh() -> None:
    conn = create_sync_connection(":memory:")
    try:
        init_schema(conn, dimension=384, model_name="BAAI/bge-small-en-v1.5")

        # Verify user_version
        ver = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert ver == 1

        # Verify tables exist
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        ]
        expected = [
            "projects",
            "facts",
            "fact_paths",
            "fact_history",
            "facts_fts",
            "facts_vec",
            "sessions",
            "episode_chunks",
            "episode_fts",
            "episode_vec",
            "tool_traces",
            "meta",
        ]
        for tbl in expected:
            assert tbl in tables, f"Expected table {tbl} missing"

        # Verify meta contents
        meta_dict = dict(conn.execute("SELECT key, value FROM meta;").fetchall())
        assert meta_dict["embedding_model"] == "BAAI/bge-small-en-v1.5"
        assert meta_dict["embedding_dimension"] == "384"
        assert "schema_created_at" in meta_dict
    finally:
        conn.close()


def test_schema_init_idempotent() -> None:
    conn = create_sync_connection(":memory:")
    try:
        init_schema(conn, dimension=384)
        init_schema(conn, dimension=384)
        ver = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert ver == 1
    finally:
        conn.close()


def test_supports_vec_partition_keys_mocked() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.execute.return_value = mock_cur

    # Mock older version v0.1.5
    mock_cur.fetchone.return_value = ("v0.1.5",)
    assert not supports_vec_partition_keys(mock_conn)

    # Mock supported version v0.1.6
    mock_cur.fetchone.return_value = ("v0.1.6",)
    assert supports_vec_partition_keys(mock_conn)

    # Mock modern version v0.1.9
    mock_cur.fetchone.return_value = ("v0.1.9",)
    assert supports_vec_partition_keys(mock_conn)


def test_schema_fallback_path() -> None:
    conn = create_sync_connection(":memory:")
    try:
        init_schema(conn, dimension=384, force_partition_keys=False)
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        ]
        assert "facts_vec" in tables
        assert "episode_vec" in tables

        meta_dict = dict(conn.execute("SELECT key, value FROM meta;").fetchall())
        assert meta_dict["vec_partition_supported"] == "0"
    finally:
        conn.close()


def test_check_meta_compatibility() -> None:
    conn = create_sync_connection(":memory:")
    try:
        init_schema(conn, dimension=384, model_name="BAAI/bge-small-en-v1.5")
        assert check_meta_compatibility(conn, "BAAI/bge-small-en-v1.5")
        assert not check_meta_compatibility(conn, "different/model")
    finally:
        conn.close()
