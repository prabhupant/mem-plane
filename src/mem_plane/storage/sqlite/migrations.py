"""Database migrations for SQLite schema."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from mem_plane.storage.sqlite.schema import compute_content_hash, init_schema


def migrate_v0_to_v1(
    conn: Any,
    dimension: int = 384,
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> bool:
    """Migrate from version 0 schema (tenant_id, HydrationBundle) to version 1 (project_id, facts).

    Returns True if migration was applied, False otherwise.
    """
    cur = conn.execute("PRAGMA user_version;")
    row = cur.fetchone()
    if row and row[0] != 0:
        return False

    # Check if facts table exists and has tenant_id column
    table_cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='facts';")
    if not table_cur.fetchone():
        return False

    col_cur = conn.execute("PRAGMA table_info(facts);")
    columns = [r[1] for r in col_cur.fetchall()]
    if "tenant_id" not in columns:
        return False

    now_iso = datetime.now(UTC).isoformat()

    # Step 1: Read rules from hydration_profiles if table exists
    rules_by_tenant: dict[str, list[str]] = {}
    has_profiles = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='hydration_profiles';"
    ).fetchone()
    if has_profiles:
        try:
            for r in conn.execute(
                "SELECT tenant_id, tenant_rules FROM hydration_profiles;"
            ).fetchall():
                t_id = str(r[0])
                rules_json = r[1]
                if rules_json:
                    try:
                        rules_by_tenant[t_id] = json.loads(rules_json)
                    except (json.JSONDecodeError, ValueError):
                        rules_by_tenant[t_id] = []
        except sqlite3.Error:
            pass

    with conn:
        # Step 2: Rename old tables
        conn.execute("ALTER TABLE facts RENAME TO facts_v0;")
        # Drop virtual tables from v0 (their data is in facts_v0 and will be re-indexed)
        try:
            conn.execute("DROP TABLE IF EXISTS facts_fts;")
        except sqlite3.Error:
            pass
        try:
            conn.execute("DROP TABLE IF EXISTS facts_vec;")
        except sqlite3.Error:
            pass

        has_traces = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_traces';"
        ).fetchone()
        if has_traces:
            conn.execute("ALTER TABLE tool_traces RENAME TO tool_traces_v0;")

        if has_profiles:
            conn.execute("DROP TABLE IF EXISTS hydration_profiles;")

        # Step 3: Initialize version 1 schema
        init_schema(conn, dimension=dimension, model_name=model_name)

        # Step 4: Convert tenant_rules into pinned facts
        for t_id, rules in rules_by_tenant.items():
            proj_id = f"p_{t_id}"
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                VALUES (?, ?, '', ?, ?);
                """,
                (proj_id, t_id, now_iso, now_iso),
            )
            for rule_text in rules:
                rule_text = rule_text.strip()
                if not rule_text:
                    continue
                f_id = f"f_{uuid.uuid4().hex[:12]}"
                c_hash = compute_content_hash(rule_text[:300], rule_text)
                conn.execute(
                    """
                    INSERT INTO facts (
                        fact_id, project_id, kind, summary, body, status,
                        pinned, source, content_hash, created_at, updated_at, embedded_at
                    ) VALUES (?, ?, 'rule', ?, ?, 'active', 1, 'user', ?, ?, ?, NULL);
                    """,
                    (f_id, proj_id, rule_text[:300], rule_text, c_hash, now_iso, now_iso),
                )
                conn.execute(
                    "INSERT INTO facts_fts (fact_id, project_id, summary, body) VALUES (?, ?, ?, ?);",
                    (f_id, proj_id, rule_text[:300], rule_text),
                )

        # Step 5: Copy facts from facts_v0
        cur_v0 = conn.execute(
            "SELECT fact_id, tenant_id, session_id, summary, raw_content, created_at FROM facts_v0;"
        )
        for r in cur_v0.fetchall():
            orig_id, t_id, s_id, summary, raw_content, created_at = r
            proj_id = f"p_{t_id}"
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                VALUES (?, ?, '', ?, ?);
                """,
                (proj_id, t_id, now_iso, now_iso),
            )
            c_hash = compute_content_hash(summary, raw_content)
            # Use original ID if available or formatted
            fact_id = orig_id if str(orig_id).startswith("f_") else f"f_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT OR IGNORE INTO facts (
                    fact_id, project_id, session_id, kind, summary, body, status,
                    pinned, source, content_hash, created_at, updated_at, embedded_at
                ) VALUES (?, ?, ?, 'note', ?, ?, 'active', 0, 'agent', ?, ?, ?, NULL);
                """,
                (fact_id, proj_id, s_id, summary, raw_content, c_hash, created_at, now_iso),
            )
            conn.execute(
                "INSERT INTO facts_fts (fact_id, project_id, summary, body) VALUES (?, ?, ?, ?);",
                (fact_id, proj_id, summary, raw_content),
            )

        # Step 6: Drop _v0 tables and finalize
        conn.execute("DROP TABLE IF EXISTS facts_v0;")
        conn.execute("DROP TABLE IF EXISTS tool_traces_v0;")
        conn.execute("PRAGMA user_version = 1;")

    return True
