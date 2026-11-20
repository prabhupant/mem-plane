"""Unit and integration tests for transcript ingestion, normalization, and chunking (Section 9.4, M4.2 - M4.9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mem_plane.config import Settings
from mem_plane.hooks.inputs import PreCompactInput, SessionEndInput, StopInput
from mem_plane.hooks.pre_compact import handle_pre_compact
from mem_plane.hooks.session_end import handle_session_end
from mem_plane.hooks.stop import handle_stop
from mem_plane.hooks.transcript import (
    ingest_transcript,
    normalize_entry,
    split_oversized_entry,
)
from mem_plane.storage.sqlite.store import SQLiteStore

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "claude_code"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "transcript_test.db"),
        chunk_target_chars=500,  # small for testing chunking boundaries
        redact=True,
    )


def test_normalize_user_and_assistant_entries() -> None:
    user_entry = {
        "type": "user",
        "timestamp": "2026-11-20T01:00:00.000Z",
        "message": {"role": "user", "content": "How do I fix this bug?"},
    }
    lines, ts, is_compact = normalize_entry(user_entry)
    assert lines == ["USER: How do I fix this bug?"]
    assert ts == "2026-11-20T01:00:00.000Z"
    assert is_compact is False

    asst_entry = {
        "type": "assistant",
        "timestamp": "2026-11-20T01:00:01.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Here is the fix."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}},
                {"type": "thinking", "thinking": "internal thought to skip"},
            ],
        },
    }
    lines, ts, is_compact = normalize_entry(asst_entry)
    assert len(lines) == 2
    assert lines[0] == "ASSISTANT: Here is the fix."
    assert lines[1] == 'TOOL CALL Bash: {"command":"git status"}'
    assert is_compact is False


def test_normalize_tool_result_with_banner() -> None:
    tool_res_entry = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": (
                        "[mem-plane: output shortened. 500 lines hidden. Trace stored as t_1234567890]\n"
                        "Some detail that was truncated"
                    ),
                }
            ],
        },
    }
    lines, _, _ = normalize_entry(tool_res_entry)
    assert len(lines) == 1
    assert (
        lines[0]
        == "TOOL RESULT: [mem-plane: output shortened. 500 lines hidden. Trace stored as t_1234567890]"
    )


def test_normalize_compaction_markers() -> None:
    boundary_entry = {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
    }
    lines, _, is_compact = normalize_entry(boundary_entry)
    assert is_compact is True
    assert lines == []

    summary_entry = {
        "type": "user",
        "isCompactSummary": True,
        "message": {"role": "user", "content": "Summary of conversation..."},
    }
    lines, _, is_compact = normalize_entry(summary_entry)
    assert is_compact is True
    assert lines == ["COMPACTION SUMMARY: Summary of conversation..."]


def test_split_oversized_entry() -> None:
    short_entry = ["USER: short line"]
    assert split_oversized_entry(short_entry, max_chars=100) == [short_entry]

    # Entry exceeding 2 * max_chars
    long_lines = [f"USER: line {i:04d} " + ("x" * 50) for i in range(10)]
    slices = split_oversized_entry(long_lines, max_chars=200)
    assert len(slices) > 1
    # Check that each slice is within reasonable boundary
    for s in slices:
        assert sum(len(l) + 1 for l in s) <= 300


def test_ingest_transcript_incremental_and_shrink_reset(
    tmp_path: Path, test_settings: Settings
) -> None:
    transcript_file = tmp_path / "session_incr.jsonl"
    session_id = "s_incr_001"
    project_id = "p_test_project"

    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        # Step 1: Write 2 entries
        entries_1 = [
            {
                "type": "user",
                "timestamp": "2026-11-20T01:00:00Z",
                "message": {"content": "Turn 1 request"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-20T01:00:01Z",
                "message": {"content": "Turn 1 response"},
            },
        ]
        with open(transcript_file, "w") as f:
            f.writelines(json.dumps(e) + "\n" for e in entries_1)

        # Run 1: Should ingest entries
        chunks_1 = ingest_transcript(
            session_id, project_id, transcript_file, store, test_settings, flush_tail=True
        )
        assert chunks_1 >= 1

        # Check session offset
        cur = store.conn.execute(
            "SELECT transcript_offset FROM sessions WHERE session_id = ?;", (session_id,)
        )
        offset_1 = cur.fetchone()[0]
        assert offset_1 == transcript_file.stat().st_size

        # Run 2: Re-ingest with no file change -> 0 new chunks
        chunks_2 = ingest_transcript(
            session_id, project_id, transcript_file, store, test_settings, flush_tail=True
        )
        assert chunks_2 == 0

        # Step 2: Append 2 new entries
        entries_2 = [
            {
                "type": "user",
                "timestamp": "2026-11-20T01:00:02Z",
                "message": {"content": "Turn 2 request"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-20T01:00:03Z",
                "message": {"content": "Turn 2 response"},
            },
        ]
        with open(transcript_file, "a") as f:
            f.writelines(json.dumps(e) + "\n" for e in entries_2)

        # Run 3: Should only ingest the newly appended entries
        chunks_3 = ingest_transcript(
            session_id, project_id, transcript_file, store, test_settings, flush_tail=True
        )
        assert chunks_3 >= 1

        cur = store.conn.execute(
            "SELECT transcript_offset FROM sessions WHERE session_id = ?;", (session_id,)
        )
        offset_2 = cur.fetchone()[0]
        assert offset_2 == transcript_file.stat().st_size
        assert offset_2 > offset_1

        # Step 3: Simulate file shrink / replacement
        with open(transcript_file, "w") as f:
            f.write(json.dumps(entries_1[0]) + "\n")

        assert transcript_file.stat().st_size < offset_2
        chunks_reset = ingest_transcript(
            session_id, project_id, transcript_file, store, test_settings, flush_tail=True
        )
        assert chunks_reset >= 1
    finally:
        store.close()


def test_ingest_transcript_redaction(tmp_path: Path, test_settings: Settings) -> None:
    transcript_file = tmp_path / "session_secrets.jsonl"
    session_id = "s_sec_001"
    project_id = "p_test_project"

    secret_key = "AKIAIOSFODNN7EXAMPLE"
    secret_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz12"
    entries = [
        {
            "type": "user",
            "timestamp": "2026-11-20T01:00:00Z",
            "message": {
                "content": f"My AWS key is {secret_key} and github is {secret_token}",
            },
        }
    ]
    with open(transcript_file, "w") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)

    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        ingest_transcript(
            session_id, project_id, transcript_file, store, test_settings, flush_tail=True
        )

        cur = store.conn.execute(
            "SELECT text FROM episode_chunks WHERE session_id = ?;", (session_id,)
        )
        row = cur.fetchone()
        assert row is not None
        chunk_text = row[0]
        assert secret_key not in chunk_text
        assert secret_token not in chunk_text
        assert "[REDACTED:aws_key]" in chunk_text
        assert "[REDACTED:github_token]" in chunk_text

        # FTS also indexed the redacted text
        cur_fts = store.conn.execute(
            "SELECT chunk_id FROM episode_fts WHERE episode_fts MATCH 'REDACTED';",
        )
        assert len(cur_fts.fetchall()) >= 1
    finally:
        store.close()


def test_ingest_with_m0_transcript_fixtures(test_settings: Settings) -> None:
    simple_fixture = FIXTURES_DIR / "transcript_simple.jsonl"
    compacted_fixture = FIXTURES_DIR / "transcript_compacted.jsonl"

    if not simple_fixture.exists() or not compacted_fixture.exists():
        pytest.skip("Transcript fixtures not found")

    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        # Ingest simple
        chunks_simple = ingest_transcript(
            session_id="s_simple_fix",
            project_id="p_test_project",
            transcript_path=simple_fixture,
            store=store,
            settings=test_settings,
        )
        assert chunks_simple >= 1

        # Ingest compacted
        chunks_compact = ingest_transcript(
            session_id="s_compact_fix",
            project_id="p_test_project",
            transcript_path=compacted_fixture,
            store=store,
            settings=test_settings,
        )
        assert chunks_compact >= 2

        # Verify segments were created for compacted fixture
        cur = store.conn.execute(
            "SELECT DISTINCT segment FROM episode_chunks WHERE session_id = 's_compact_fix' ORDER BY segment;",
        )
        segments = [r[0] for r in cur.fetchall()]
        assert len(segments) >= 2, f"Expected multiple segments, got {segments}"
    finally:
        store.close()


def test_stop_precompact_and_session_end_hooks(tmp_path: Path, test_settings: Settings) -> None:
    transcript_file = tmp_path / "hook_flow.jsonl"
    session_id = "s_hooks_flow"

    with open(transcript_file, "w") as f:
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-11-20T01:00:00Z",
                    "message": {"content": "Initial prompt"},
                }
            )
            + "\n"
        )

    # 1. handle_stop
    stop_payload = StopInput(
        session_id=session_id,
        cwd=str(tmp_path),
        transcript_path=str(transcript_file),
    )
    assert handle_stop(stop_payload, test_settings) is None

    # Check session exists and chunk stored
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        cur = store.conn.execute(
            "SELECT count(*) FROM episode_chunks WHERE session_id = ?;", (session_id,)
        )
        assert cur.fetchone()[0] == 1

        # 2. handle_pre_compact
        pre_compact_payload = PreCompactInput(
            session_id=session_id,
            cwd=str(tmp_path),
            transcript_path=str(transcript_file),
        )
        assert handle_pre_compact(pre_compact_payload, test_settings) is None

        # Check compactions incremented
        cur = store.conn.execute(
            "SELECT compactions FROM sessions WHERE session_id = ?;", (session_id,)
        )
        assert cur.fetchone()[0] == 1

        # 3. handle_session_end
        end_payload = SessionEndInput(
            session_id=session_id,
            cwd=str(tmp_path),
            transcript_path=str(transcript_file),
        )
        assert handle_session_end(end_payload, test_settings) is None

        # Check ended_at is set
        cur = store.conn.execute(
            "SELECT ended_at FROM sessions WHERE session_id = ?;", (session_id,)
        )
        row = cur.fetchone()
        assert row is not None and row[0] is not None
    finally:
        store.close()


def test_transcript_two_compactions_completeness() -> None:
    """M4.11 verification: 100% completeness and 0 duplicate chunks on 2-compaction session."""
    from eval.transcript.completeness import check_completeness

    fixture = FIXTURES_DIR / "transcript_two_compactions.jsonl"
    assert fixture.exists()
    passed, stats = check_completeness(fixture)
    assert passed is True
    assert stats["completeness_rate"] == 100.0
    assert stats["duplicate_chunks"] == 0
    assert stats["segment_count"] == 3
