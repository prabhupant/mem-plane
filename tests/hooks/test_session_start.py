"""Unit and integration tests for SessionStart context injection (Section 9.7, M5.1 - M5.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mem_plane.config import Settings
from mem_plane.core.models import Fact, FactKind
from mem_plane.hooks.inputs import SessionStartInput
from mem_plane.hooks.session_start import USAGE_HINT, handle_session_start
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "session_start_test.db"),
        session_context_max_chars=6000,
        recent_facts=8,
    )


def test_session_start_empty_project_prints_only_usage_hint(
    tmp_path: Path, test_settings: Settings
) -> None:
    payload = SessionStartInput(
        session_id="s_empty_001",
        cwd=str(tmp_path),
        source="startup",
    )
    result = handle_session_start(payload, test_settings)
    assert result is not None
    assert "hookSpecificOutput" in result
    hso = result["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert hso["additionalContext"] == USAGE_HINT


def test_session_start_sections_and_deterministic_sorting(
    tmp_path: Path, test_settings: Settings
) -> None:
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    proj = resolve_project(str(tmp_path))
    session_id = "s_active_001"
    now = datetime.now(UTC)

    try:
        # 1. Pinned facts
        f_rule = Fact(
            fact_id="f_rule001",
            project_id=proj.project_id,
            kind=FactKind.RULE,
            summary="All integers in minor units; never floats.",
            body="Money convention",
            pinned=True,
            created_at=now - timedelta(days=5),
        )
        f_conv = Fact(
            fact_id="f_conv001",
            project_id=proj.project_id,
            kind=FactKind.CONVENTION,
            summary="Tests run with make test.",
            body="Testing convention",
            pinned=True,
            created_at=now - timedelta(days=4),
        )
        f_dec = Fact(
            fact_id="f_dec001",
            project_id=proj.project_id,
            kind=FactKind.DECISION,
            summary="Postgres is the only datastore.",
            body="Architecture decision",
            pinned=True,
            created_at=now - timedelta(days=3),
        )
        store.create_fact(f_rule)
        store.create_fact(f_conv)
        store.create_fact(f_dec)

        # 2. Session facts (recorded in this session)
        f_sess1 = Fact(
            fact_id="f_sess001",
            project_id=proj.project_id,
            session_id=session_id,
            kind=FactKind.DECISION,
            summary="JWT access tokens expire after 15 minutes.",
            body="Auth policy",
            pinned=False,
            created_at=now - timedelta(minutes=30),
        )
        f_sess2 = Fact(
            fact_id="f_sess002",
            project_id=proj.project_id,
            session_id=session_id,
            kind=FactKind.BUGFIX,
            summary="Flaky test_refund fixed by freezing time in conftest.",
            body="Bug fix note",
            pinned=False,
            created_at=now - timedelta(minutes=20),
        )
        store.create_fact(f_sess1)
        store.create_fact(f_sess2)

        # 3. Recent facts from earlier sessions
        for i in range(10):
            f_old = Fact(
                fact_id=f"f_old{i:03d}",
                project_id=proj.project_id,
                session_id="s_old_session",
                kind=FactKind.NOTE,
                summary=f"Historical note {i} on system behavior.",
                body="Details",
                pinned=False,
                created_at=now - timedelta(days=10 + i),
                updated_at=now - timedelta(days=i),
            )
            store.create_fact(f_old)

        store.upsert_session(session_id, proj.project_id, None)
        store.increment_compactions(session_id)
        store.increment_compactions(session_id)

    finally:
        store.close()

    # Test startup source (should not show "Recorded earlier in this session")
    payload_startup = SessionStartInput(
        session_id=session_id,
        cwd=str(tmp_path),
        source="startup",
    )
    res_startup = handle_session_start(payload_startup, test_settings)
    assert res_startup is not None
    ctx_startup = res_startup["hookSpecificOutput"]["additionalContext"]
    assert "Pinned:" in ctx_startup
    assert "Recorded earlier in this session:" not in ctx_startup
    assert "Recent:" in ctx_startup
    assert USAGE_HINT in ctx_startup

    # Test compact source (should show "Recorded earlier in this session")
    payload_compact = SessionStartInput(
        session_id=session_id,
        cwd=str(tmp_path),
        source="compact",
    )
    res_compact = handle_session_start(payload_compact, test_settings)
    assert res_compact is not None
    ctx_compact = res_compact["hookSpecificOutput"]["additionalContext"]

    assert "Pinned:" in ctx_compact
    assert "Recorded earlier in this session:" in ctx_compact
    assert "f_sess001" in ctx_compact
    assert "f_sess002" in ctx_compact
    assert "Recent:" in ctx_compact
    assert "2 compactions so far" in ctx_compact

    # Determinism test: calling it again produces byte-identical output
    res_compact_2 = handle_session_start(payload_compact, test_settings)
    assert res_compact_2 is not None
    assert res_compact_2["hookSpecificOutput"]["additionalContext"] == ctx_compact


def test_session_start_budget_enforcement_with_500_facts(
    tmp_path: Path, test_settings: Settings
) -> None:
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    proj = resolve_project(str(tmp_path))
    session_id = "s_budget_001"
    now = datetime.now(UTC)

    try:
        # Create 50 pinned facts
        for i in range(50):
            store.create_fact(
                Fact(
                    fact_id=f"f_pin{i:03d}",
                    project_id=proj.project_id,
                    kind=FactKind.RULE,
                    summary=f"Pinned rule {i:03d} " + ("x" * 120),
                    body="Rule details",
                    pinned=True,
                    created_at=now - timedelta(days=100 - i),
                )
            )

        # Create 50 session facts
        for i in range(50):
            store.create_fact(
                Fact(
                    fact_id=f"f_sess{i:03d}",
                    project_id=proj.project_id,
                    session_id=session_id,
                    kind=FactKind.DECISION,
                    summary=f"Session decision {i:03d} " + ("y" * 120),
                    body="Decision details",
                    pinned=False,
                    created_at=now - timedelta(minutes=100 - i),
                )
            )

        # Create 400 other active facts
        for i in range(400):
            store.create_fact(
                Fact(
                    fact_id=f"f_hist{i:03d}",
                    project_id=proj.project_id,
                    session_id="s_other",
                    kind=FactKind.NOTE,
                    summary=f"Historical fact {i:03d} " + ("z" * 120),
                    body="Historical details",
                    pinned=False,
                    created_at=now - timedelta(days=200 - i),
                    updated_at=now - timedelta(days=200 - i),
                )
            )
    finally:
        store.close()

    payload = SessionStartInput(
        session_id=session_id,
        cwd=str(tmp_path),
        source="compact",
    )
    result = handle_session_start(payload, test_settings)
    assert result is not None
    ctx = result["hookSpecificOutput"]["additionalContext"]

    # Budget strictly respected
    assert len(ctx) <= test_settings.session_context_max_chars
    # Pinned beyond 20 dropped
    assert "f_pin020" in ctx or "f_pin019" in ctx
    assert "f_pin049" not in ctx


def test_session_start_factual_tone_and_updates_root_path(
    tmp_path: Path, test_settings: Settings
) -> None:
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    proj = resolve_project(str(tmp_path))
    try:
        store.create_fact(
            Fact(
                fact_id="f_fact1",
                project_id=proj.project_id,
                kind=FactKind.RULE,
                summary="Sample rule",
                body="Body",
                pinned=True,
            )
        )
    finally:
        store.close()

    transcript_path = str(tmp_path / "test_session.jsonl")
    payload = SessionStartInput(
        session_id="s_tone_001",
        cwd=str(tmp_path),
        transcript_path=transcript_path,
        source="startup",
    )
    result = handle_session_start(payload, test_settings)
    assert result is not None
    ctx = result["hookSpecificOutput"]["additionalContext"]

    # Factual statements, not commands
    assert "You must" not in ctx
    assert "CRITICAL:" not in ctx
    assert "IMPORTANT:" not in ctx
    assert "mem-plane memory for project" in ctx

    # Check store session updated transcript_path
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        cur = store.conn.execute(
            "SELECT transcript_path FROM sessions WHERE session_id = 's_tone_001';"
        )
        assert cur.fetchone()[0] == transcript_path
    finally:
        store.close()
