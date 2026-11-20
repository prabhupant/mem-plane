"""UserPromptSubmit hook: optional automatic keyword memory recall (Section 9.8)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from mem_plane.hooks.inputs import UserPromptSubmitInput
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.connection import create_sync_connection
from mem_plane.storage.sqlite.search import build_fts5_query

if TYPE_CHECKING:
    from mem_plane.config import Settings


def handle_user_prompt_submit(
    payload: UserPromptSubmitInput,
    settings: Settings,
) -> dict[str, Any] | None:
    """Run keyword-only search over active facts and inject relevant memories if passing threshold."""
    # Fast exit if auto_recall is disabled (off by default)
    if not settings.auto_recall:
        return None

    prompt_text = payload.prompt.strip()
    if not prompt_text:
        return None

    fts_query = build_fts5_query(prompt_text, max_tokens=16)
    if not fts_query:
        return None

    project_id = settings.project_id or resolve_project(payload.cwd).project_id

    conn = create_sync_connection(settings.db_path, load_vec=False)
    try:
        # FTS5 bm25 returns negative values (more negative = better match)
        # -bm25 gives positive score where higher is better
        cur = conn.execute(
            """
            SELECT f.fact_id, f.summary, -bm25(facts_fts, 0.0, 0.0, 2.0, 1.0) AS score
            FROM facts_fts
            JOIN facts f ON f.fact_id = facts_fts.fact_id
            WHERE facts_fts MATCH ?
              AND facts_fts.project_id = ?
              AND f.status = 'active'
            ORDER BY score DESC, f.fact_id ASC
            LIMIT 3;
            """,
            (fts_query, project_id),
        )
        rows = cur.fetchall()
    except (sqlite3.Error, OSError):
        return None
    finally:
        conn.close()

    passing_hits: list[str] = []
    for fact_id, summary, score in rows:
        if score is not None and float(score) >= settings.auto_recall_min_score:
            clean_summary = summary.replace("\n", " ").strip()
            passing_hits.append(f"{fact_id} {clean_summary}")

    if not passing_hits:
        return None

    prefix = "mem-plane: possibly relevant memories: "
    joined = "; ".join(passing_hits)
    text = prefix + joined

    # At most 800 characters
    if len(text) > 800:
        text = text[:797] + "..."

    return {
        "hookSpecificOutput": {
            "additionalContext": text,
        }
    }
