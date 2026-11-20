"""Tests for Fact, SearchHit, TraceRecord, and model validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mem_plane.core.models import (
    Fact,
    FactKind,
    FactPath,
    FactStatus,
    SearchHit,
    SurrogateHit,
    TraceRecord,
)


def test_fact_model_defaults() -> None:
    now = datetime.now(UTC)
    fact = Fact(
        fact_id="f_123456789012",
        project_id="p_test1234",
        kind=FactKind.DECISION,
        summary="Use SQLite with FTS5 and sqlite-vec.",
        body="Zero-infrastructure storage for single-machine local agent.",
    )

    assert fact.status == FactStatus.ACTIVE
    assert not fact.pinned
    assert fact.source == "agent"
    assert fact.paths == []
    assert fact.created_at <= datetime.now(UTC)
    assert fact.updated_at >= now


def test_fact_summary_max_length() -> None:
    long_summary = "a" * 301
    with pytest.raises(ValidationError):
        Fact(
            fact_id="f_123456789012",
            project_id="p_test1234",
            kind=FactKind.RULE,
            summary=long_summary,
            body="Body text",
        )


def test_fact_paths() -> None:
    path = FactPath(rel_path="src/main.py", content_sha256="abc123hash", git_commit="c0ffee")
    fact = Fact(
        fact_id="f_123456789012",
        project_id="p_test1234",
        kind=FactKind.BUGFIX,
        summary="Fix deadlock by using busy_timeout.",
        body="Set PRAGMA busy_timeout=5000.",
        paths=[path],
    )
    assert len(fact.paths) == 1
    assert fact.paths[0].rel_path == "src/main.py"


def test_search_hit_model() -> None:
    now = datetime.now(UTC)
    hit = SearchHit(
        ref="f_123456789012",
        ref_type="fact",
        summary="Short summary",
        score=0.95,
        kind="decision",
        stale=False,
        pinned=True,
        created_at=now,
    )
    assert hit.ref == "f_123456789012"
    assert hit.pinned


def test_trace_record_model() -> None:
    now = datetime.now(UTC)
    trace = TraceRecord(
        trace_id="t_0123456789",
        project_id="p_test1234",
        session_id="sess_123",
        tool_name="Bash",
        command="pytest",
        stdout="passed",
        stderr="",
        exit_code=0,
        line_count=1,
        char_count=6,
        created_at=now,
    )
    assert trace.trace_id == "t_0123456789"
    assert trace.exit_code == 0


def test_surrogate_hit_deprecated_alias() -> None:
    # Verify backward compatibility alias exists and works
    sh = SurrogateHit(fact_id="f_old", summary="old summary", score=0.8)
    assert sh.fact_id == "f_old"
