"""Unit tests for background fact extractor with mocked model (Section 9.6, M7.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from mem_plane.config import Settings
from mem_plane.pipeline.extractor import FactExtractor, parse_extracted_facts
from mem_plane.storage.sqlite.store import SQLiteStore


@dataclass
class MockContentBlock:
    text: str


@dataclass
class MockResponse:
    content: list[MockContentBlock]


class MockAnthropicClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.messages = MagicMock()
        self.messages.create.return_value = MockResponse([MockContentBlock(response_text)])


def test_parse_extracted_facts_clean_json() -> None:
    raw = json.dumps(
        [
            {
                "kind": "decision",
                "summary": "Adopt SQLite with sqlite-vec as primary datastore",
                "body": "Avoid external distributed vector database dependencies for local harness.",
                "paths": ["src/mem_plane/storage/sqlite/store.py"],
            },
            {
                "kind": "rule",
                "summary": "All prices must be integers in cents",
                "body": "Never use float for money values.",
            },
            {
                "kind": "invalid_kind",
                "summary": "This should be discarded",
                "body": "Invalid kind discarded.",
            },
        ]
    )
    facts = parse_extracted_facts(raw)
    assert len(facts) == 2
    assert facts[0]["kind"] == "decision"
    assert facts[0]["paths"] == ["src/mem_plane/storage/sqlite/store.py"]
    assert facts[1]["kind"] == "rule"


def test_parse_extracted_facts_markdown_fenced() -> None:
    raw = """Here is the extracted information:

```json
[
  {
    "kind": "bugfix",
    "summary": "Fix race condition in transcript reader with BEGIN IMMEDIATE",
    "body": "Acquiring an immediate write transaction prevents sqlite busy errors during compactions.",
    "paths": ["src/mem_plane/hooks/transcript.py"]
  }
]
```

Hope this helps!"""
    facts = parse_extracted_facts(raw)
    assert len(facts) == 1
    assert facts[0]["kind"] == "bugfix"
    assert "BEGIN IMMEDIATE" in facts[0]["summary"]


def test_parse_extracted_facts_caps_at_8() -> None:
    many = [
        {
            "kind": "convention",
            "summary": f"Convention {i}",
            "body": f"Detailed body for convention {i}",
        }
        for i in range(15)
    ]
    facts = parse_extracted_facts(json.dumps(many))
    assert len(facts) == 8


def test_parse_extracted_facts_invalid_json() -> None:
    assert parse_extracted_facts("") == []
    assert parse_extracted_facts("not a valid json") == []
    assert parse_extracted_facts('{"error": "not a list"}') == []


def test_extractor_extract_from_text_source_and_pinned(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "memory.db"), extractor="anthropic")
    store = SQLiteStore(database=str(tmp_path / "memory.db"), dimension=384, settings=settings)
    try:
        extractor = FactExtractor(settings, store)
        mock_response = json.dumps(
            [
                {
                    "kind": "decision",
                    "summary": "Use WAL mode for SQLite",
                    "body": "WAL mode allows concurrent readers while a write is occurring.",
                }
            ]
        )
        mock_client = MockAnthropicClient(mock_response)

        facts = extractor.extract_from_text(
            project_id="p_test123",
            session_id="s_test456",
            text="Let's make sure we use WAL mode for sqlite concurrency.",
            client=mock_client,
        )

        assert len(facts) == 1
        f = facts[0]
        assert f.source == "extractor"
        assert f.pinned is False
        assert f.project_id == "p_test123"
        assert f.session_id == "s_test456"
        assert f.kind == "decision"
    finally:
        store.close()


def test_extractor_process_closed_segment(tmp_path: Path) -> None:
    db_file = tmp_path / "memory.db"
    settings = Settings(db_path=str(db_file), extractor="anthropic")
    store = SQLiteStore(database=str(db_file), dimension=384, settings=settings)
    try:
        from mem_plane.project import resolve_project

        proj = resolve_project(str(tmp_path))
        store.upsert_project(proj)
        session_id = "sess_close_001"
        store.upsert_session(session_id, proj.project_id, transcript_path=None)

        now_str = datetime.now(UTC).isoformat()
        # Insert chunks for closed segment 1
        chunk_text = (
            "USER: How should we test our order validation?\n"
            "ASSISTANT: We decided in the design review that all test cases should run "
            "using pytest with the --strict-markers flag enabled. Never skip validation."
        )
        store.conn.execute(
            """
            INSERT INTO episode_chunks (
                chunk_id, project_id, session_id, seq, segment,
                start_ts, end_ts, text, token_estimate, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "ep_000000000001",
                proj.project_id,
                session_id,
                0,
                1,  # segment 1
                now_str,
                now_str,
                chunk_text,
                50,
                now_str,
            ),
        )
        store.conn.commit()

        extractor = FactExtractor(settings, store)
        mock_payload = json.dumps(
            [
                {
                    "kind": "convention",
                    "summary": "Run pytest with --strict-markers flag",
                    "body": "All test cases must use pytest with --strict-markers enabled.",
                    "paths": ["pytest.ini"],
                }
            ]
        )
        mock_client = MockAnthropicClient(mock_payload)

        created_facts = extractor.process_segment(
            session_id=session_id,
            segment=1,
            client=mock_client,
        )

        assert len(created_facts) == 1
        fact = created_facts[0]
        assert fact.kind == "convention"
        assert fact.source == "extractor"
        assert fact.pinned is False

        # Verify fact was saved to the store
        saved = store.get_fact(fact.fact_id, proj.project_id)
        assert saved is not None
        assert saved.summary == "Run pytest with --strict-markers flag"
        assert saved.source == "extractor"

        # Verify running again does not duplicate
        second_run = extractor.process_segment(
            session_id=session_id,
            segment=1,
            client=mock_client,
        )
        assert len(second_run) == 0  # Deduped by create_fact
    finally:
        store.close()
