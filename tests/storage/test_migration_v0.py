"""Tests for schema migration from version 0 to version 1."""

from __future__ import annotations

import json
from pathlib import Path

from mem_plane.storage.sqlite.connection import create_sync_connection
from mem_plane.storage.sqlite.migrations import migrate_v0_to_v1


def test_migration_v0_to_v1(tmp_path: Path) -> None:
    db_file = tmp_path / "v0_test.db"
    conn = create_sync_connection(db_file)

    # 1. Create a version 0 database
    conn.execute(
        """
        CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            session_id TEXT,
            summary TEXT NOT NULL,
            raw_content TEXT NOT NULL,
            source_type TEXT NOT NULL,
            provenance_metadata TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE hydration_profiles (
            tenant_id TEXT PRIMARY KEY,
            tenant_rules TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE tool_traces (
            trace_id TEXT PRIMARY KEY,
            raw_trace TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    # Insert v0 facts
    conn.execute(
        """
        INSERT INTO facts VALUES (
            'fact-001', 'alpha-team', 'sess-1',
            'Use Redis for session caching', 'Redis configured with 10m TTL in auth_service.',
            'codebase', '{}', '2026-10-01T12:00:00Z'
        );
        """
    )

    # Insert v0 hydration profile with rules
    rules = ["Always format with ruff", "Tests must pass before commit"]
    conn.execute(
        "INSERT INTO hydration_profiles VALUES ('alpha-team', ?);",
        (json.dumps(rules),),
    )

    conn.execute("PRAGMA user_version = 0;")
    conn.commit()

    # 2. Run migration
    migrated = migrate_v0_to_v1(conn, dimension=384)
    assert migrated is True

    # 3. Verify user_version is 1
    ver = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert ver == 1

    # 4. Verify projects table has p_alpha-team
    cur_p = conn.execute("SELECT project_id, display_name FROM projects;")
    projects = cur_p.fetchall()
    assert len(projects) == 1
    assert projects[0][0] == "p_alpha-team"
    assert projects[0][1] == "alpha-team"

    # 5. Verify facts copied
    cur_f = conn.execute(
        "SELECT summary, body, kind, status, pinned, embedded_at FROM facts WHERE kind='note';"
    )
    fact_row = cur_f.fetchone()
    assert fact_row is not None
    assert fact_row[0] == "Use Redis for session caching"
    assert fact_row[1] == "Redis configured with 10m TTL in auth_service."
    assert fact_row[2] == "note"
    assert fact_row[3] == "active"
    assert fact_row[4] == 0
    assert fact_row[5] is None  # embedded_at NULL so embedder will process it

    # 6. Verify tenant rules turned into pinned facts
    cur_r = conn.execute("SELECT summary, body, kind, pinned FROM facts WHERE kind='rule';")
    rule_rows = cur_r.fetchall()
    assert len(rule_rows) == 2
    for r in rule_rows:
        assert r[2] == "rule"
        assert r[3] == 1  # pinned

    # 7. Verify old tables dropped
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    ]
    assert "facts_v0" not in tables
    assert "hydration_profiles" not in tables
    assert "tool_traces_v0" not in tables

    conn.close()
