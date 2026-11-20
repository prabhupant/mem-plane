"""PreCompact hook handler for capturing transcript before context compaction (Section 9.5)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mem_plane.hooks.transcript import ingest_transcript
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.hooks.inputs import PreCompactInput

logger = logging.getLogger(__name__)


def handle_pre_compact(payload: PreCompactInput, settings: Settings) -> dict[str, Any] | None:
    """Capture dialogue prior to compaction, increment segment. Never blocks or prints."""
    try:
        proj_info = resolve_project(payload.cwd)
        store = SQLiteStore(database=settings.db_path, load_vec=False)
        try:
            store.upsert_project(proj_info)
            ingest_transcript(
                session_id=payload.session_id,
                project_id=proj_info.project_id,
                transcript_path=payload.transcript_path,
                store=store,
                settings=settings,
                flush_tail=True,
            )
            store.increment_compactions(payload.session_id)
        finally:
            store.close()
    except Exception:
        logger.exception("Error in handle_pre_compact for session %s", payload.session_id)

    return None
