"""SessionStart hook handler for memory index injection (Section 9.7)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.hooks.inputs import SessionStartInput

logger = logging.getLogger(__name__)

USAGE_HINT = (
    "Memory tools: search_memory finds earlier decisions and conversation, "
    "get_memory(id) returns full detail, record_fact saves durable decisions, "
    "rules, and bug causes."
)


def _truncate_summary(summary: str, max_len: int = 160) -> str:
    """Truncate summary to max_len characters."""
    s = summary.strip().replace("\n", " ")
    return s[:max_len]


def build_session_context(
    store: SQLiteStore,
    project_id: str,
    session_id: str,
    source: str,
    display_name: str,
    settings: Settings,
) -> str:
    """Build deterministic SessionStart memory index according to Section 9.7."""
    # 1. Total counts
    cur = store.conn.execute(
        "SELECT count(*) FROM facts WHERE project_id = ? AND status = 'active';",
        (project_id,),
    )
    fact_count = int(cur.fetchone()[0])

    cur = store.conn.execute(
        "SELECT count(*) FROM facts WHERE project_id = ? AND status = 'active' AND pinned = 1;",
        (project_id,),
    )
    pinned_count = int(cur.fetchone()[0])

    cur = store.conn.execute(
        "SELECT count(*) FROM episode_chunks WHERE project_id = ?;",
        (project_id,),
    )
    chunk_count = int(cur.fetchone()[0])

    # If project has no facts and no chunks, return only the usage hint (§9.7)
    if fact_count == 0 and chunk_count == 0:
        return USAGE_HINT

    # 2. Query Pinned facts: sort by kind ASC, created_at ASC, fact_id ASC
    cur = store.conn.execute(
        """
        SELECT fact_id, kind, summary
        FROM facts
        WHERE project_id = ? AND status = 'active' AND pinned = 1
        ORDER BY kind ASC, created_at ASC, fact_id ASC;
        """,
        (project_id,),
    )
    pinned_rows = cur.fetchall()
    pinned_items = [f"- {r[0]} [{r[1]}] {_truncate_summary(r[2])}" for r in pinned_rows]
    seen_ids = {r[0] for r in pinned_rows}

    # 3. Query Session facts (only when source == "compact")
    session_items: list[str] = []
    earlier_convo_note = ""
    is_compact = source.lower() == "compact"

    if is_compact:
        cur = store.conn.execute(
            """
            SELECT fact_id, kind, summary
            FROM facts
            WHERE project_id = ? AND session_id = ? AND status = 'active' AND pinned = 0
            ORDER BY created_at ASC, fact_id ASC;
            """,
            (project_id, session_id),
        )
        session_rows = cur.fetchall()
        for r in session_rows:
            if r[0] not in seen_ids:
                session_items.append(f"- {r[0]} [{r[1]}] {_truncate_summary(r[2])}")
                seen_ids.add(r[0])

        cur_comp = store.conn.execute(
            "SELECT compactions FROM sessions WHERE session_id = ?;",
            (session_id,),
        )
        comp_row = cur_comp.fetchone()
        compactions = int(comp_row[0]) if comp_row else 0
        comp_word = "compaction" if compactions == 1 else "compactions"
        earlier_convo_note = (
            f"This session's earlier conversation is archived and searchable "
            f"({compactions} {comp_word} so far)."
        )

    # 4. Query Recent facts: up to recent_facts entries
    # Active, unpinned facts not already listed, sorted by updated_at DESC, fact_id ASC
    cur = store.conn.execute(
        """
        SELECT fact_id, kind, summary, created_at
        FROM facts
        WHERE project_id = ? AND status = 'active' AND pinned = 0
        ORDER BY updated_at DESC, fact_id ASC;
        """,
        (project_id,),
    )
    recent_rows = cur.fetchall()
    recent_items: list[str] = []
    for r in recent_rows:
        if r[0] not in seen_ids and len(recent_items) < settings.recent_facts:
            date_str = str(r[3])[:10]
            recent_items.append(f"- {r[0]} [{r[1]}] {_truncate_summary(r[2])} ({date_str})")
            seen_ids.add(r[0])

    # 5. Helper to assemble text
    header = (
        f"mem-plane memory for project {display_name} "
        f"({fact_count} facts, {pinned_count} pinned, {chunk_count} archived transcript chunks)."
    )

    def assemble(
        p_list: list[str],
        s_list: list[str],
        r_list: list[str],
    ) -> str:
        sections: list[str] = [header]

        if p_list:
            sections.append("Pinned:\n" + "\n".join(p_list))

        if is_compact:
            compact_parts: list[str] = []
            if s_list:
                compact_parts.append("Recorded earlier in this session:\n" + "\n".join(s_list))
            if earlier_convo_note:
                compact_parts.append(earlier_convo_note)
            if compact_parts:
                sections.append("\n".join(compact_parts))

        if r_list:
            sections.append("Recent:\n" + "\n".join(r_list))

        sections.append(USAGE_HINT)
        return "\n\n".join(sections)

    # 6. Budget enforcement (§9.7)
    max_chars = settings.session_context_max_chars

    # Drop Recent entries from the bottom first
    while len(assemble(pinned_items, session_items, recent_items)) > max_chars and recent_items:
        recent_items.pop()

    # Drop session facts from the bottom next
    while len(assemble(pinned_items, session_items, recent_items)) > max_chars and session_items:
        session_items.pop()

    # Drop pinned facts beyond the first 20
    if len(pinned_items) > 20:
        pinned_items = pinned_items[:20]

    while (
        len(assemble(pinned_items, session_items, recent_items)) > max_chars
        and len(pinned_items) > 1
    ):
        pinned_items.pop()

    return assemble(pinned_items, session_items, recent_items)


def handle_session_start(
    payload: SessionStartInput,
    settings: Settings,
) -> dict[str, Any] | None:
    """Handle SessionStart hook event by injecting memory index context."""
    try:
        proj_info = resolve_project(payload.cwd)
        store = SQLiteStore(database=settings.db_path, load_vec=False)
        try:
            # Update project root and session transcript path (§9.7)
            store.upsert_project(proj_info)
            store.upsert_session(
                session_id=payload.session_id,
                project_id=proj_info.project_id,
                transcript_path=payload.transcript_path,
            )

            context_text = build_session_context(
                store=store,
                project_id=proj_info.project_id,
                session_id=payload.session_id,
                source=payload.source,
                display_name=proj_info.display_name,
                settings=settings,
            )

            return {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context_text,
                }
            }
        finally:
            store.close()
    except Exception:
        logger.exception("Error in handle_session_start for session %s", payload.session_id)
        return None
