"""PostToolUse hook handler for pruning large Bash tool outputs (Section 9.2)."""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mem_plane.core.models import TraceRecord
from mem_plane.hooks.preview import build_preview
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.hooks.inputs import PostToolUseInput

logger = logging.getLogger(__name__)


def handle_post_tool_use(payload: PostToolUseInput, settings: Settings) -> dict[str, Any] | None:
    """Handle PostToolUse hook event.

    Returns dict to be emitted as JSON, or None to emit nothing.
    Never breaks the harness, never blocks.
    """
    if not settings.prune_enabled:
        return None

    # Only prune Bash tool
    if payload.tool_name != "Bash":
        return None

    # Validate tool_response shape
    resp = payload.tool_response
    if not isinstance(resp, dict):
        return None

    # tool_response for Bash must contain stdout or stderr strings
    if "stdout" not in resp and "stderr" not in resp:
        return None

    stdout: str = str(resp.get("stdout") or "")
    stderr: str = str(resp.get("stderr") or "")

    # Check for Claude Code persistedOutputPath when stdout was truncated in-memory
    persisted_path = resp.get("persistedOutputPath")
    if persisted_path and isinstance(persisted_path, str):
        try:
            p_file = Path(persisted_path)
            if p_file.is_file():
                stdout = p_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

    cmd: str = str(payload.tool_input.get("command") or "").strip()

    # Exclusion regex check
    exclude_pattern = settings.prune_exclude_regex
    if exclude_pattern and re.search(exclude_pattern, cmd):
        return None

    stdout_lines = len(stdout.splitlines())
    stderr_lines = len(stderr.splitlines())
    total_lines = stdout_lines + stderr_lines
    total_chars = len(stdout) + len(stderr)

    # Threshold checks
    if total_lines <= settings.prune_max_lines and total_chars <= settings.prune_max_chars:
        return None

    # Resolve project & open store
    proj_info = resolve_project(payload.cwd)
    store = SQLiteStore(
        database=settings.db_path,
        dimension=settings.embedding_dimension,
        settings=settings,
        load_vec=False,
    )

    try:
        store.upsert_project(proj_info)
        store.upsert_session(
            session_id=payload.session_id,
            project_id=proj_info.project_id,
            transcript_path=payload.transcript_path,
        )

        trace_id = f"t_{secrets.token_hex(5)}"

        exit_code: int | None = None
        if "exitCode" in resp and isinstance(resp["exitCode"], int):
            exit_code = resp["exitCode"]
        elif "exit_code" in resp and isinstance(resp["exit_code"], int):
            exit_code = resp["exit_code"]

        # Save complete trace in store
        trace = TraceRecord(
            trace_id=trace_id,
            project_id=proj_info.project_id,
            session_id=payload.session_id,
            tool_name=payload.tool_name,
            command=cmd,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            line_count=total_lines,
            char_count=total_chars,
        )
        store.save_trace(trace, retention_days=settings.trace_retention_days)

        # Per-stream "large" check: stream alone exceeds half of either threshold
        half_lines = settings.prune_max_lines // 2
        half_chars = settings.prune_max_chars // 2

        stdout_large = stdout_lines > half_lines or len(stdout) > half_chars
        stderr_large = stderr_lines > half_lines or len(stderr) > half_chars

        new_stdout = (
            build_preview(
                stdout,
                trace_id=trace_id,
                stream="stdout",
                exit_code=exit_code,
                settings=settings,
            )
            if stdout_large
            else stdout
        )

        new_stderr = (
            build_preview(
                stderr,
                trace_id=trace_id,
                stream="stderr",
                exit_code=exit_code,
                settings=settings,
            )
            if stderr_large
            else stderr
        )

        updated_tool_output = {
            **resp,
            "stdout": new_stdout,
            "stderr": new_stderr,
        }

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": updated_tool_output,
            }
        }
    finally:
        store.close()
