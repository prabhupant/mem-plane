"""Incremental transcript ingestion, normalization, chunking, and archiving (Section 9.4)."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mem_plane.core.redact import redact_secrets

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.storage.sqlite.store import SQLiteStore

logger = logging.getLogger(__name__)


def normalize_entry(entry: dict[str, Any]) -> tuple[list[str], str | None, bool]:
    """Convert a Claude Code transcript JSON entry into normalized plain text lines.

    Returns:
        (lines, timestamp, is_compaction)
    """
    ts = entry.get("timestamp")
    timestamp = str(ts) if isinstance(ts, str) else None

    # 1. Compaction boundary & summary detection
    etype = entry.get("type")
    subtype = entry.get("subtype")

    if etype == "system" and subtype == "compact_boundary":
        return [], timestamp, True

    if entry.get("isCompactSummary") is True:
        raw_msg = entry.get("message")
        msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else entry
        raw_content = msg.get("content", "")
        if isinstance(raw_content, str):
            text = raw_content.strip()
        elif isinstance(raw_content, list):
            parts = [
                b.get("text", "")
                for b in raw_content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = "\n".join(parts).strip()
        else:
            text = str(raw_content).strip()
        return [f"COMPACTION SUMMARY: {text}"], timestamp, True

    # 2. User messages
    if etype == "user" or (
        isinstance(entry.get("message"), dict) and entry["message"].get("role") == "user"
    ):
        raw_msg = entry.get("message")
        msg = raw_msg if isinstance(raw_msg, dict) else entry
        content = msg.get("content")
        out_lines: list[str] = []

        if isinstance(content, str):
            s = content.strip()
            if s:
                out_lines.append(f"USER: {s}")
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        out_lines.append(f"USER: {txt}")
                elif btype == "tool_result":
                    raw = block.get("content", "")
                    if isinstance(raw, list):
                        parts = []
                        for b in raw:
                            if isinstance(b, dict) and b.get("type") == "text":
                                parts.append(b.get("text", ""))
                            elif isinstance(b, str):
                                parts.append(b)
                        raw = "\n".join(parts)
                    elif not isinstance(raw, str):
                        raw = str(raw)

                    if "[mem-plane: output shortened." in raw:
                        banner = ""
                        for line in raw.splitlines():
                            if "[mem-plane: output shortened." in line:
                                banner = line.strip()
                                break
                        out_lines.append(f"TOOL RESULT: {banner}")
                    else:
                        first_500 = raw[:500].strip()
                        out_lines.append(f"TOOL RESULT: {first_500}")
        return out_lines, timestamp, False

    # 3. Assistant messages
    if etype == "assistant" or (
        isinstance(entry.get("message"), dict) and entry["message"].get("role") == "assistant"
    ):
        raw_msg = entry.get("message")
        msg = raw_msg if isinstance(raw_msg, dict) else entry
        content = msg.get("content")
        out_lines = []

        if isinstance(content, str):
            s = content.strip()
            if s:
                out_lines.append(f"ASSISTANT: {s}")
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        out_lines.append(f"ASSISTANT: {txt}")
                elif btype == "tool_use":
                    name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    try:
                        compact_json = json.dumps(
                            tool_input, separators=(",", ":"), ensure_ascii=False
                        )
                    except (TypeError, ValueError):
                        compact_json = str(tool_input)
                    if len(compact_json) > 300:
                        compact_json = compact_json[:297] + "..."
                    out_lines.append(f"TOOL CALL {name}: {compact_json}")
                # Thinking blocks, images, and other types are skipped (§9.4)
        return out_lines, timestamp, False

    # 4. Other types (session-owner, queue-operation, attachments, etc.) are skipped
    return [], timestamp, False


def split_oversized_entry(entry_lines: list[str], max_chars: int) -> list[list[str]]:
    """Split an entry that exceeds twice the target size at line boundaries."""
    total_len = sum(len(line) + 1 for line in entry_lines)
    if total_len <= 2 * max_chars:
        return [entry_lines]

    # Decompose into individual lines
    individual_lines: list[str] = []
    for item in entry_lines:
        individual_lines.extend(item.splitlines())

    slices: list[list[str]] = []
    current_slice: list[str] = []
    current_len = 0

    for line in individual_lines:
        line_len = len(line) + 1
        if current_slice and (current_len + line_len > max_chars):
            slices.append(current_slice)
            current_slice = [line]
            current_len = line_len
        else:
            current_slice.append(line)
            current_len += line_len

    if current_slice:
        slices.append(current_slice)

    return slices if slices else [entry_lines]


def ingest_transcript(
    session_id: str,
    project_id: str,
    transcript_path: str | Path | None,
    store: SQLiteStore,
    settings: Settings,
    flush_tail: bool = True,
) -> int:
    """Incrementally read and archive transcript lines into episode chunks.

    Guarantees:
    - Atomicity with BEGIN IMMEDIATE
    - Offset tracking with file-shrink reset
    - Defensive normalization per Section 9.4
    - Redaction applied before storage
    - Never raises
    """
    if not transcript_path:
        return 0

    t_path = Path(transcript_path).expanduser().resolve()
    if not t_path.is_file():
        return 0

    # Ensure session row exists
    store.upsert_session(session_id, project_id, str(t_path))

    prev_iso = store.conn.isolation_level
    store.conn.isolation_level = None

    try:
        store.conn.execute("BEGIN IMMEDIATE;")

        cur = store.conn.execute(
            "SELECT transcript_offset, compactions FROM sessions WHERE session_id = ?;",
            (session_id,),
        )
        row = cur.fetchone()
        offset = int(row[0]) if row else 0
        current_segment = int(row[1]) if row else 0

        st_size = t_path.stat().st_size
        if st_size < offset:
            logger.warning(
                "Transcript file %s shrank from %d to %d; resetting offset to 0",
                t_path,
                offset,
                st_size,
            )
            offset = 0

        with open(t_path, "rb") as f:
            f.seek(offset)
            raw_bytes = f.read()

        if not raw_bytes:
            store.conn.execute("COMMIT;")
            return 0

        last_nl = raw_bytes.rfind(b"\n")
        if last_nl == -1:
            # Incomplete line; do not process yet
            store.conn.execute("COMMIT;")
            return 0

        bytes_to_process = raw_bytes[: last_nl + 1]
        new_offset = offset + len(bytes_to_process)
        text_content = bytes_to_process.decode("utf-8", errors="replace")

        # Query max sequence number
        cur = store.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM episode_chunks WHERE session_id = ?;",
            (session_id,),
        )
        next_seq = int(cur.fetchone()[0]) + 1

        chunk_target = settings.chunk_target_chars
        chunks_created = 0

        buffer_lines: list[str] = []
        buf_start_ts: str | None = None
        buf_end_ts: str | None = None

        def flush_chunk(lines: list[str], start_ts: str | None, end_ts: str | None) -> None:
            nonlocal next_seq, chunks_created
            if not lines:
                return

            chunk_text = "\n\n".join(lines)
            redacted_text = redact_secrets(chunk_text, enabled=settings.redact)
            tok_est = len(redacted_text) // 4
            now_iso = datetime.now(UTC).isoformat()
            chunk_id = f"c_{secrets.token_hex(5)}"

            store.conn.execute(
                """
                INSERT INTO episode_chunks (
                    chunk_id, project_id, session_id, seq, segment,
                    start_ts, end_ts, text, token_estimate, created_at, embedded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL);
                """,
                (
                    chunk_id,
                    project_id,
                    session_id,
                    next_seq,
                    current_segment,
                    start_ts,
                    end_ts,
                    redacted_text,
                    tok_est,
                    now_iso,
                ),
            )
            store.conn.execute(
                "INSERT INTO episode_fts (chunk_id, project_id, text) VALUES (?, ?, ?);",
                (chunk_id, project_id, redacted_text),
            )
            next_seq += 1
            chunks_created += 1

        just_compacted = False

        for raw_line in text_content.splitlines():
            line_str = raw_line.strip()
            if not line_str:
                continue

            try:
                entry = json.loads(line_str)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue

            if not isinstance(entry, dict):
                continue

            lines, entry_ts, is_compaction = normalize_entry(entry)
            if not lines and not is_compaction:
                continue

            if is_compaction:
                # Flush previous buffer before incrementing segment
                if buffer_lines:
                    flush_chunk(buffer_lines, buf_start_ts, buf_end_ts)
                    buffer_lines = []
                    buf_start_ts = None
                    buf_end_ts = None

                if not just_compacted:
                    current_segment += 1
                    just_compacted = True
                    store.conn.execute(
                        "UPDATE sessions SET compactions = ?, last_event_at = ? WHERE session_id = ?;",
                        (current_segment, datetime.now(UTC).isoformat(), session_id),
                    )
            else:
                just_compacted = False

            if not lines:
                continue

            # Oversized entry splitting if exceeding 2 * chunk_target
            sub_slices = split_oversized_entry(lines, chunk_target)

            for sub_slice in sub_slices:
                sub_len = sum(len(l) + 1 for l in sub_slice)
                buf_len = sum(len(l) + 1 for l in buffer_lines)

                if buffer_lines and (buf_len + sub_len >= chunk_target):
                    # Cut chunk at end of current entry / buffer
                    flush_chunk(buffer_lines, buf_start_ts, buf_end_ts)
                    buffer_lines = list(sub_slice)
                    buf_start_ts = entry_ts
                    buf_end_ts = entry_ts
                else:
                    if not buffer_lines:
                        buf_start_ts = entry_ts
                    buf_end_ts = entry_ts or buf_end_ts
                    buffer_lines.extend(sub_slice)

        if flush_tail and buffer_lines:
            flush_chunk(buffer_lines, buf_start_ts, buf_end_ts)
            buffer_lines = []

        now_iso = datetime.now(UTC).isoformat()
        store.conn.execute(
            """
            UPDATE sessions
            SET transcript_offset = ?, last_event_at = ?, compactions = ?
            WHERE session_id = ?;
            """,
            (new_offset, now_iso, current_segment, session_id),
        )

        store.conn.execute("COMMIT;")
        return chunks_created

    except Exception:
        logger.exception("Error in ingest_transcript for session %s", session_id)
        try:
            store.conn.execute("ROLLBACK;")
        except Exception:  # noqa: S110, BLE001
            pass
        return 0
    finally:
        store.conn.isolation_level = prev_iso
