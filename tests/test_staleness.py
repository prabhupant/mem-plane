"""Tests for staleness checking logic (Section 7.6)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from mem_plane.core.models import Fact, FactKind, FactPath
from mem_plane.core.staleness import check_fact_staleness, clear_staleness_cache


def test_staleness_unchanged_file(tmp_path: Path) -> None:
    clear_staleness_cache()
    src_file = tmp_path / "app.py"
    content = b"print('hello')"
    src_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    fact = Fact(
        fact_id="f_stale001",
        project_id="p1",
        kind=FactKind.RULE,
        summary="Test rule",
        body="rule body",
        paths=[FactPath(rel_path="app.py", content_sha256=expected_hash)],
    )

    reason = check_fact_staleness(fact, tmp_path)
    assert reason is None


def test_staleness_changed_file(tmp_path: Path) -> None:
    clear_staleness_cache()
    src_file = tmp_path / "config.py"
    src_file.write_bytes(b"JWT_SECRET='old'")

    fact = Fact(
        fact_id="f_stale002",
        project_id="p1",
        kind=FactKind.RULE,
        summary="Test rule",
        body="rule body",
        paths=[FactPath(rel_path="config.py", content_sha256="old_fake_sha256")],
    )

    reason = check_fact_staleness(fact, tmp_path)
    assert reason == "config.py changed"


def test_staleness_deleted_file(tmp_path: Path) -> None:
    clear_staleness_cache()
    fact = Fact(
        fact_id="f_stale003",
        project_id="p1",
        kind=FactKind.RULE,
        summary="Test rule",
        body="rule body",
        paths=[FactPath(rel_path="missing.py", content_sha256="dummy_sha256")],
    )

    reason = check_fact_staleness(fact, tmp_path)
    assert reason == "missing.py deleted"


def test_staleness_large_file_skipped(tmp_path: Path) -> None:
    clear_staleness_cache()
    large_file = tmp_path / "large.bin"
    # Create file slightly over 5MB
    large_file.write_bytes(b"0" * (5 * 1024 * 1024 + 10))

    fact = Fact(
        fact_id="f_stale004",
        project_id="p1",
        kind=FactKind.CONTEXT,
        summary="Large file context",
        body="Large file body",
        paths=[FactPath(rel_path="large.bin", content_sha256="should_be_skipped")],
    )

    reason = check_fact_staleness(fact, tmp_path)
    # Skipped files are treated as not stale
    assert reason is None


def test_staleness_cache_invalidation_by_mtime(tmp_path: Path) -> None:
    clear_staleness_cache()
    src_file = tmp_path / "cached.py"
    content1 = b"v1"
    src_file.write_bytes(content1)
    hash1 = hashlib.sha256(content1).hexdigest()

    fact = Fact(
        fact_id="f_stale005",
        project_id="p1",
        kind=FactKind.RULE,
        summary="Cached test",
        body="body",
        paths=[FactPath(rel_path="cached.py", content_sha256=hash1)],
    )

    # First check: matches hash1 -> not stale
    assert check_fact_staleness(fact, tmp_path) is None

    # Update file content and mtime
    time.sleep(0.01)
    content2 = b"v2"
    src_file.write_bytes(content2)

    # Second check: file mtime/size changed, cache invalidated -> now stale
    assert check_fact_staleness(fact, tmp_path) == "cached.py changed"
