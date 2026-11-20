"""SQLite schema definitions, DDL execution, and sqlite-vec compatibility."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any


def compute_content_hash(summary: str, body: str) -> str:
    """Normalize text (trim, collapse internal whitespace) and compute SHA-256."""
    norm_summary = re.sub(r"\s+", " ", summary.strip())
    norm_body = re.sub(r"\s+", " ", body.strip())
    combined = f"{norm_summary}\n{norm_body}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def parse_semver(ver_str: str) -> tuple[int, int, int]:
    """Parse semver string like 'v0.1.9' or '0.1.6' into a 3-tuple."""
    clean = ver_str.strip().lstrip("v")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", clean)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def supports_vec_partition_keys(conn: Any) -> bool:
    """Check if sqlite-vec is version >= 0.1.6 (supports partition keys and metadata columns)."""
    try:
        cur = conn.execute("SELECT vec_version();")
        row = cur.fetchone()
        if not row or not row[0]:
            return False
        ver = parse_semver(str(row[0]))
        return ver >= (0, 1, 6)
    except (AttributeError, sqlite3.Error, OSError):
        return False


def get_schema_v1_ddl(
    dimension: int = 384,
    partition_keys: bool = True,
    include_vec: bool = True,
) -> list[str]:
    """Return SQL DDL statements for schema version 1."""
    ddl = [
        # ---------- projects ----------
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id      TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL,
            root_path       TEXT NOT NULL,
            remote_url      TEXT,
            created_at      TEXT NOT NULL,
            last_seen_at    TEXT NOT NULL
        );
        """,
        # ---------- facts ----------
        """
        CREATE TABLE IF NOT EXISTS facts (
            fact_id         TEXT PRIMARY KEY,
            project_id      TEXT NOT NULL REFERENCES projects(project_id),
            session_id      TEXT,
            kind            TEXT NOT NULL CHECK (kind IN
                                ('decision','rule','bugfix','convention','context','note')),
            summary         TEXT NOT NULL,
            body            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','superseded','deleted')),
            supersedes      TEXT REFERENCES facts(fact_id),
            superseded_by   TEXT REFERENCES facts(fact_id),
            pinned          INTEGER NOT NULL DEFAULT 0,
            source          TEXT NOT NULL CHECK (source IN ('agent','extractor','user')),
            content_hash    TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            embedded_at     TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_facts_project_status ON facts(project_id, status);",
        "CREATE INDEX IF NOT EXISTS idx_facts_project_pinned ON facts(project_id, pinned) WHERE pinned = 1;",
        "CREATE INDEX IF NOT EXISTS idx_facts_project_updated ON facts(project_id, updated_at DESC);",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_dedup ON facts(project_id, content_hash)
            WHERE status = 'active';
        """,
        # ---------- fact_paths ----------
        """
        CREATE TABLE IF NOT EXISTS fact_paths (
            fact_id         TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
            rel_path        TEXT NOT NULL,
            content_sha256  TEXT,
            git_commit      TEXT,
            PRIMARY KEY (fact_id, rel_path)
        );
        """,
        # ---------- fact_history ----------
        """
        CREATE TABLE IF NOT EXISTS fact_history (
            history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id         TEXT NOT NULL,
            event           TEXT NOT NULL CHECK (event IN
                                ('create','update','supersede','delete','pin','unpin')),
            old_summary     TEXT,
            new_summary     TEXT,
            old_body        TEXT,
            new_body        TEXT,
            actor           TEXT NOT NULL,
            session_id      TEXT,
            created_at      TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_fact_history_fact ON fact_history(fact_id, created_at);",
        # ---------- facts_fts ----------
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
            fact_id UNINDEXED,
            project_id UNINDEXED,
            summary,
            body,
            tokenize = 'porter unicode61'
        );
        """,
    ]

    # ---------- facts_vec ----------
    if include_vec:
        if partition_keys:
            ddl.append(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
                    fact_id TEXT PRIMARY KEY,
                    project_id TEXT PARTITION KEY,
                    status TEXT,
                    embedding FLOAT[{dimension}] DISTANCE_METRIC=cosine
                );
                """
            )
        else:
            ddl.append(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
                    fact_id TEXT PRIMARY KEY,
                    embedding FLOAT[{dimension}] DISTANCE_METRIC=cosine
                );
                """
            )

    # ---------- sessions and episodes ----------
    ddl.extend(
        [
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id          TEXT PRIMARY KEY,
                project_id          TEXT NOT NULL REFERENCES projects(project_id),
                harness             TEXT NOT NULL DEFAULT 'claude-code',
                transcript_path     TEXT,
                transcript_offset   INTEGER NOT NULL DEFAULT 0,
                compactions         INTEGER NOT NULL DEFAULT 0,
                started_at          TEXT NOT NULL,
                last_event_at       TEXT NOT NULL,
                ended_at            TEXT
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, last_event_at DESC);",
            """
            CREATE TABLE IF NOT EXISTS episode_chunks (
                chunk_id        TEXT PRIMARY KEY,
                project_id      TEXT NOT NULL REFERENCES projects(project_id),
                session_id      TEXT NOT NULL REFERENCES sessions(session_id),
                seq             INTEGER NOT NULL,
                segment         INTEGER NOT NULL,
                start_ts        TEXT,
                end_ts          TEXT,
                text            TEXT NOT NULL,
                token_estimate  INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                embedded_at     TEXT,
                UNIQUE (session_id, seq)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_chunks_project ON episode_chunks(project_id, created_at DESC);",
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
                chunk_id UNINDEXED,
                project_id UNINDEXED,
                text,
                tokenize = 'porter unicode61'
            );
            """,
        ]
    )

    # ---------- episode_vec ----------
    if include_vec:
        if partition_keys:
            ddl.append(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS episode_vec USING vec0(
                    chunk_id TEXT PRIMARY KEY,
                    project_id TEXT PARTITION KEY,
                    embedding FLOAT[{dimension}] DISTANCE_METRIC=cosine
                );
                """
            )
        else:
            ddl.append(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS episode_vec USING vec0(
                    chunk_id TEXT PRIMARY KEY,
                    embedding FLOAT[{dimension}] DISTANCE_METRIC=cosine
                );
                """
            )

    # ---------- traces and metadata ----------
    ddl.extend(
        [
            """
            CREATE TABLE IF NOT EXISTS tool_traces (
                trace_id        TEXT PRIMARY KEY,
                project_id      TEXT NOT NULL REFERENCES projects(project_id),
                session_id      TEXT,
                tool_name       TEXT NOT NULL,
                tool_use_id     TEXT,
                command         TEXT,
                stdout          TEXT NOT NULL,
                stderr          TEXT NOT NULL,
                exit_code       INTEGER,
                line_count      INTEGER NOT NULL,
                char_count      INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                expires_at      TEXT NOT NULL
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_traces_project ON tool_traces(project_id, created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_traces_expiry ON tool_traces(expires_at);",
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """,
        ]
    )
    return ddl


def init_schema(
    conn: Any,
    dimension: int = 384,
    model_name: str = "BAAI/bge-small-en-v1.5",
    force_partition_keys: bool | None = None,
    include_vec: bool = True,
) -> None:
    """Initialize schema version 1 and populate metadata."""
    if force_partition_keys is not None:
        partition_keys = force_partition_keys
    else:
        partition_keys = supports_vec_partition_keys(conn)

    ddl_statements = get_schema_v1_ddl(
        dimension=dimension,
        partition_keys=partition_keys,
        include_vec=include_vec,
    )

    for stmt in ddl_statements:
        conn.execute(stmt)

    # Populate meta if missing
    cur = conn.execute("SELECT value FROM meta WHERE key = 'embedding_model';")
    if not cur.fetchone():
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_model', ?);", (model_name,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_dimension', ?);",
            (str(dimension),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_created_at', ?);", (now,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('vec_partition_supported', ?);",
            ("1" if partition_keys else "0",),
        )

    conn.execute("PRAGMA user_version = 1;")
    conn.commit()


def check_meta_compatibility(conn: Any, configured_model: str) -> bool:
    """Verify that configured embedding model matches meta.embedding_model."""
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key = 'embedding_model';")
        row = cur.fetchone()
        if not row:
            return True
        return str(row[0]) == configured_model
    except (AttributeError, sqlite3.Error, OSError):
        return True
