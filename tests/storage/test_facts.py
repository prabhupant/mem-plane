"""Tests for Fact CRUD, dedup, supersede chains, and project isolation."""

from __future__ import annotations

from pathlib import Path

from mem_plane.core.models import Fact, FactKind, FactPath, FactStatus
from mem_plane.storage.sqlite.store import SQLiteStore


def test_fact_create_and_get(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "facts.db")
    try:
        fact = Fact(
            fact_id="f_test00000001",
            project_id="p_proj1",
            kind=FactKind.DECISION,
            summary="Use SQLite with WAL mode.",
            body="Configured busy_timeout to 5000 and synchronous to NORMAL.",
            paths=[FactPath(rel_path="src/main.py", content_sha256="abc", git_commit="rev1")],
        )

        res = store.create_fact(fact)
        assert res.status == "created"
        assert res.fact.fact_id == "f_test00000001"

        fetched = store.get_fact("f_test00000001", "p_proj1")
        assert fetched is not None
        assert fetched.summary == "Use SQLite with WAL mode."
        assert len(fetched.paths) == 1
        assert fetched.paths[0].rel_path == "src/main.py"
    finally:
        store.close()


def test_fact_dedup_by_hash(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "dedup.db")
    try:
        fact1 = Fact(
            fact_id="f_orig00000001",
            project_id="p_proj1",
            kind=FactKind.RULE,
            summary="Always run mypy in strict mode.",
            body="Ensure no untyped defs.",
        )
        res1 = store.create_fact(fact1)
        assert res1.status == "created"

        # Fact with same content but slightly different whitespace
        fact2 = Fact(
            fact_id="f_dup000000002",
            project_id="p_proj1",
            kind=FactKind.RULE,
            summary="  Always  run mypy in strict mode.  ",
            body="Ensure no untyped defs.\n",
        )
        res2 = store.create_fact(fact2)
        assert res2.status == "duplicate"
        assert res2.duplicate_of_id == "f_orig00000001"
        assert res2.fact.fact_id == "f_orig00000001"
    finally:
        store.close()


def test_fact_supersede_chain(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "supersede.db")
    try:
        fact1 = Fact(
            fact_id="f_old00000001",
            project_id="p_proj1",
            kind=FactKind.DECISION,
            summary="Access tokens expire after 60 minutes.",
            body="Original token lifespan decision.",
        )
        store.create_fact(fact1)

        fact2 = Fact(
            fact_id="f_new00000002",
            project_id="p_proj1",
            kind=FactKind.DECISION,
            summary="Access tokens expire after 15 minutes.",
            body="Shortened for security compliance.",
            supersedes="f_old00000001",
        )
        res2 = store.create_fact(fact2)
        assert res2.status == "superseded"
        assert res2.superseded_fact is not None
        assert res2.superseded_fact.fact_id == "f_old00000001"

        # Verify old fact is superseded
        old_fact = store.get_fact("f_old00000001", "p_proj1")
        assert old_fact is not None
        assert old_fact.status == FactStatus.SUPERSEDED
        assert old_fact.superseded_by == "f_new00000002"

        # Check history rows
        history = store.fact_history("f_old00000001", "p_proj1")
        events = [h.event for h in history]
        assert "create" in events
        assert "supersede" in events
    finally:
        store.close()


def test_fact_update_and_history(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "update.db")
    try:
        fact = Fact(
            fact_id="f_upd00000001",
            project_id="p_proj1",
            kind=FactKind.BUGFIX,
            summary="Flaky test fixed.",
            body="Root cause under investigation.",
        )
        store.create_fact(fact)

        updated = store.update_fact(
            "f_upd00000001",
            "p_proj1",
            summary="Flaky test fixed by freezing time.",
            body="Timezone drift caused test to fail at midnight.",
            actor="agent",
        )
        assert updated.summary == "Flaky test fixed by freezing time."

        history = store.fact_history("f_upd00000001", "p_proj1")
        assert len(history) == 2
        assert history[0].event == "create"
        assert history[1].event == "update"
        assert history[1].old_summary == "Flaky test fixed."
        assert history[1].new_summary == "Flaky test fixed by freezing time."
    finally:
        store.close()


def test_fact_soft_delete(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "delete.db")
    try:
        fact = Fact(
            fact_id="f_del00000001",
            project_id="p_proj1",
            kind=FactKind.NOTE,
            summary="Temporary dev note.",
            body="Should be deleted after sprint.",
        )
        store.create_fact(fact)
        assert store.get_fact("f_del00000001", "p_proj1") is not None

        store.delete_fact("f_del00000001", "p_proj1", actor="user")
        assert store.get_fact("f_del00000001", "p_proj1") is None

        # Verify history row for delete
        history = store.fact_history("f_del00000001", "p_proj1")
        assert any(h.event == "delete" for h in history)
    finally:
        store.close()


def test_fact_pin_and_list_pinned(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "pin.db")
    try:
        fact = Fact(
            fact_id="f_pin00000001",
            project_id="p_proj1",
            kind=FactKind.RULE,
            summary="Never commit directly to master.",
            body="Always use feature branches.",
            pinned=False,
        )
        store.create_fact(fact)
        assert len(store.list_pinned("p_proj1")) == 0

        store.set_pinned("f_pin00000001", "p_proj1", True)
        pinned = store.list_pinned("p_proj1")
        assert len(pinned) == 1
        assert pinned[0].fact_id == "f_pin00000001"

        store.set_pinned("f_pin00000001", "p_proj1", False)
        assert len(store.list_pinned("p_proj1")) == 0
    finally:
        store.close()


def test_project_isolation(tmp_path: Path) -> None:
    """A fact from project A is NEVER returned for project B by search or ID (M1.10)."""
    store = SQLiteStore(database=tmp_path / "isolation.db")
    try:
        fact_a = Fact(
            fact_id="f_projA0000001",
            project_id="p_projectA",
            kind=FactKind.DECISION,
            summary="Confidential project A secret decision.",
            body="Proprietary algorithm details.",
        )
        store.create_fact(fact_a)

        # Lookup by ID with project B returns None
        assert store.get_fact("f_projA0000001", "p_projectB") is None

        # List recent / pinned with project B returns empty
        assert len(store.list_recent_facts("p_projectB")) == 0
        assert len(store.list_pinned("p_projectB")) == 0

        # History with project B returns empty
        assert len(store.fact_history("f_projA0000001", "p_projectB")) == 0

        # Lookup by ID with project A returns the fact
        assert store.get_fact("f_projA0000001", "p_projectA") is not None
    finally:
        store.close()
