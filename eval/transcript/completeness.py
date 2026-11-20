"""Transcript archive completeness verification script (Section 15.1, M4.10, M4.11).

Compares every user and assistant message in a transcript file with the stored
episode chunks in SQLite, verifying 100% capture and zero duplicate chunks.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from mem_plane.config import Settings
from mem_plane.core.redact import redact_secrets
from mem_plane.hooks.transcript import ingest_transcript
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore


def extract_messages_from_transcript(transcript_path: Path) -> list[dict[str, Any]]:
    """Extract all user and assistant textual messages from a transcript."""
    messages: list[dict[str, Any]] = []

    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue

            try:
                entry = json.loads(line_str)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue

            if not isinstance(entry, dict):
                continue

            etype = entry.get("type")
            if etype not in ("user", "assistant"):
                continue

            raw_msg = entry.get("message")
            msg_obj: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else entry
            role = etype

            # Skip compact summaries from the raw user prompt count if desired, or include them
            content = msg_obj.get("content")
            if isinstance(content, str):
                s = content.strip()
                if s:
                    messages.append(
                        {
                            "line": line_num,
                            "role": role,
                            "text": s,
                            "is_compact_summary": entry.get("isCompactSummary", False),
                        }
                    )
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        txt = block.get("text", "").strip()
                        if txt:
                            messages.append(
                                {
                                    "line": line_num,
                                    "role": role,
                                    "text": txt,
                                    "is_compact_summary": entry.get("isCompactSummary", False),
                                }
                            )

    return messages


def check_completeness(
    transcript_path: Path,
    db_path: str | Path | None = None,
    session_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Run completeness and dedup audit for a transcript against stored chunks."""
    cfg = settings or Settings()
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory()
        actual_db_path = str(Path(temp_dir.name) / "eval_completeness.db")
    else:
        actual_db_path = str(db_path)

    try:
        # Determine session ID from transcript if not provided
        if not session_id:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if "sessionId" in d:
                            session_id = d["sessionId"]
                            break
                        if "session_id" in d:
                            session_id = d["session_id"]
                            break
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                        continue
        if not session_id:
            session_id = f"s_eval_{transcript_path.stem}"

        project = resolve_project(str(transcript_path.parent))
        store = SQLiteStore(database=actual_db_path, load_vec=False)
        try:
            # Ingest transcript
            ingest_transcript(
                session_id=session_id,
                project_id=project.project_id,
                transcript_path=transcript_path,
                store=store,
                settings=cfg,
                flush_tail=True,
            )

            # Retrieve all chunks for this session
            cur = store.conn.execute(
                """
                SELECT chunk_id, seq, segment, text, token_estimate
                FROM episode_chunks
                WHERE session_id = ?
                ORDER BY seq ASC;
                """,
                (session_id,),
            )
            chunks = cur.fetchall()

            # Check duplicate chunks
            chunk_texts = [r[3] for r in chunks]
            text_counts = Counter(chunk_texts)
            duplicate_chunks = sum(count - 1 for count in text_counts.values() if count > 1)

            # Check distinct segments
            cur_seg = store.conn.execute(
                "SELECT count(DISTINCT segment) FROM episode_chunks WHERE session_id = ?;",
                (session_id,),
            )
            segment_count = cur_seg.fetchone()[0]

            # Aggregate chunk text for message matching
            all_chunks_text = "\n\n".join(chunk_texts)

            # Extract raw messages
            raw_messages = extract_messages_from_transcript(transcript_path)
            matched_count = 0
            missing_messages: list[dict[str, Any]] = []

            for msg in raw_messages:
                target_text = msg["text"]
                redacted_target = redact_secrets(target_text, enabled=cfg.redact)

                # Check if entire redacted text or its key lines exist in stored chunks
                if redacted_target in all_chunks_text:
                    matched_count += 1
                else:
                    # Check first 200 chars if multiline
                    sample = redacted_target[:200].strip()
                    if sample and sample in all_chunks_text:
                        matched_count += 1
                    else:
                        missing_messages.append(msg)

            total_messages = len(raw_messages)
            completeness_rate = (
                (matched_count / total_messages * 100.0) if total_messages > 0 else 100.0
            )

            passed = (completeness_rate >= 100.0) and (duplicate_chunks == 0)

            stats = {
                "transcript": str(transcript_path),
                "session_id": session_id,
                "total_messages": total_messages,
                "matched_messages": matched_count,
                "completeness_rate": completeness_rate,
                "chunk_count": len(chunks),
                "segment_count": segment_count,
                "duplicate_chunks": duplicate_chunks,
                "missing": missing_messages,
                "passed": passed,
            }
            return passed, stats
        finally:
            store.close()
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify transcript archive completeness")
    parser.add_argument("transcript", nargs="?", help="Path to transcript .jsonl file")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--session-id", help="Session ID")
    args = parser.parse_args()

    default_path = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "fixtures"
        / "claude_code"
        / "transcript_compacted.jsonl"
    )
    transcript_file = Path(args.transcript) if args.transcript else default_path

    if not transcript_file.exists():
        print(f"Error: Transcript file not found at {transcript_file}", file=sys.stderr)
        return 1

    passed, stats = check_completeness(
        transcript_path=transcript_file,
        db_path=args.db,
        session_id=args.session_id,
    )

    print("=" * 70)
    print("TRANSCRIPT ARCHIVE COMPLETENESS REPORT")
    print("=" * 70)
    print(f"Transcript file:     {stats['transcript']}")
    print(f"Session ID:          {stats['session_id']}")
    print(f"Total messages:      {stats['total_messages']}")
    print(f"Matched messages:    {stats['matched_messages']}")
    print(f"Completeness:        {stats['completeness_rate']:.1f}%")
    print(f"Chunks created:      {stats['chunk_count']}")
    print(f"Segments:            {stats['segment_count']}")
    print(f"Duplicate chunks:    {stats['duplicate_chunks']}")
    print("=" * 70)
    print(f"STATUS: {'PASS' if passed else 'FAIL'}")
    print("=" * 70)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
