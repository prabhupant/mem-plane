"""Stop hook handler for archiving each turn (Section 9.3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mem_plane.hooks.transcript import ingest_transcript
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.hooks.inputs import StopInput

logger = logging.getLogger(__name__)


def handle_stop(payload: StopInput, settings: Settings) -> dict[str, Any] | None:
    """Archive current turn into episode chunks. Never prints anything."""
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
        finally:
            store.close()
    except Exception:
        logger.exception("Error in handle_stop for session %s", payload.session_id)

    return None
