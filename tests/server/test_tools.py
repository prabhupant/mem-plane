"""Unit tests for the 7 memory tools via MemPlaneService (Section 8.2, 15.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mem_plane.core.models import FactKind, TraceRecord
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.server.service import MemPlaneService
from mem_plane.storage.sqlite.store import SQLiteStore


@pytest.fixture
def service_setup(tmp_path: Path) -> MemPlaneService:
    db_path = tmp_path / "tools_test.db"
    store = SQLiteStore(database=db_path)
    client = DummyEmbeddingClient(dimension=384)
    service = MemPlaneService(
        store=store,
        project_id="p_test_tools",
        embedding_client=client,
        project_root=str(tmp_path),
    )
    return service


@pytest.mark.asyncio
async def test_record_fact_and_duplicate(service_setup: MemPlaneService) -> None:
    svc = service_setup

    # 1. Record fact
    res1 = await svc.record_fact(
        summary="Use PostgreSQL with pgvector for production",
        body="Detailed architecture decision for persistence.",
        kind="decision",
        pin=True,
    )
    assert res1.startswith("recorded f_")
    fact_id = res1.split()[1]

    # 2. Record duplicate
    res_dup = await svc.record_fact(
        summary="Use PostgreSQL with pgvector for production",
        body="Detailed architecture decision for persistence.",
        kind="decision",
    )
    assert res_dup == f"duplicate of {fact_id}"


@pytest.mark.asyncio
async def test_record_fact_path_validation(service_setup: MemPlaneService, tmp_path: Path) -> None:
    svc = service_setup
    (tmp_path / "src").mkdir()
    code_file = tmp_path / "src" / "app.py"
    code_file.write_text("DATABASE_URL = 'sqlite:///test.db'")

    # Valid relative path inside root
    res = await svc.record_fact(
        summary="Database configuration",
        body="Config location",
        kind=FactKind.CONVENTION,
        paths=["src/app.py"],
    )
    assert res.startswith("recorded f_")

    # Path attempting directory traversal outside root
    res_err = await svc.record_fact(
        summary="Malicious path",
        body="Trying to escape root",
        kind="convention",
        paths=["../../etc/passwd"],
    )
    assert res_err.startswith("error: path '../../etc/passwd' is outside project root")


@pytest.mark.asyncio
async def test_record_fact_supersedes(service_setup: MemPlaneService) -> None:
    svc = service_setup

    res1 = await svc.record_fact(
        summary="Initial token TTL was 1 hour",
        body="Old auth token expiry.",
        kind="decision",
    )
    f1_id = res1.split()[1]

    res2 = await svc.record_fact(
        summary="Shorten token TTL to 15 minutes",
        body="New auth token expiry for better security.",
        kind="decision",
        supersedes=f1_id,
    )
    assert res2.startswith("recorded f_")
    assert f"superseding {f1_id}" in res2


@pytest.mark.asyncio
async def test_get_memory_fact_detail(service_setup: MemPlaneService, tmp_path: Path) -> None:
    svc = service_setup
    (tmp_path / "rules").mkdir(exist_ok=True)
    rule_file = tmp_path / "rules" / "mypy.ini"
    rule_file.write_text("[mypy]\nstrict = True\n")

    res = await svc.record_fact(
        summary="Strict mypy checking is mandatory",
        body="All modules must pass mypy --strict.",
        kind="rule",
        paths=["rules/mypy.ini"],
        pin=True,
    )
    fact_id = res.split()[1]

    detail = await svc.get_memory(fact_id)
    assert fact_id in detail
    assert "[rule]" in detail
    assert "[PINNED]" in detail
    assert "Strict mypy checking is mandatory" in detail
    assert "rules/mypy.ini" in detail


@pytest.mark.asyncio
async def test_get_memory_trace_filtering(service_setup: MemPlaneService) -> None:
    svc = service_setup

    # Insert a synthetic tool trace
    stdout_lines = [
        "Starting test runner v2.1",
        "running test_auth ... ok",
        "running test_crypto ... FAILED",
        "  Traceback (most recent call last):",
        "    File 'test_crypto.py', line 42, in test_crypto",
        "      assert secret == 'expected'",
        "AssertionError: mismatch",
        "running test_db ... ok",
        "1 failed, 2 passed in 0.4s",
    ]
    trace = TraceRecord(
        trace_id="t_testtrace01",
        project_id=svc.project_id,
        tool_name="Bash",
        command="pytest",
        stdout="\n".join(stdout_lines),
        stderr="",
        exit_code=1,
        line_count=len(stdout_lines),
        char_count=len("\n".join(stdout_lines)),
    )
    svc.store.save_trace(trace)

    # 1. Default retrieval
    full_out = await svc.get_memory("t_testtrace01", max_chars=1000)
    assert "t_testtrace01" in full_out
    assert "=== STDOUT" in full_out
    assert "running test_crypto ... FAILED" in full_out

    # 2. Grep filter for 'AssertionError'
    grep_out = await svc.get_memory("t_testtrace01", grep="AssertionError")
    assert ">     7 | AssertionError: mismatch" in grep_out
    assert "assert secret == 'expected'" in grep_out  # 1 line context before
    assert "running test_db ... ok" in grep_out  # 1 line context after
    assert "Starting test runner" not in grep_out  # outside context

    # 3. Line range slice: lines="3-5"
    lines_out = await svc.get_memory("t_testtrace01", lines="3-5")
    assert "3 | running test_crypto ... FAILED" in lines_out
    assert "4 |   Traceback" in lines_out
    assert "5 |     File" in lines_out
    assert "Starting test runner" not in lines_out

    # 4. Truncation when output exceeds max_chars
    trunc_out = await svc.get_memory("t_testtrace01", max_chars=50)
    assert "Output truncated to 50 chars" in trunc_out


@pytest.mark.asyncio
async def test_update_forget_pin_lifecycle(service_setup: MemPlaneService) -> None:
    svc = service_setup

    # Record
    rec = await svc.record_fact(
        summary="Temporary workaround for SQLite lock",
        body="Added busy_timeout=1000.",
        kind="bugfix",
    )
    fact_id = rec.split()[1]

    # Pin / Unpin
    p_res = await svc.pin_memory(fact_id, pinned=True)
    assert p_res == f"pinned {fact_id}"
    unp_res = await svc.pin_memory(fact_id, pinned=False)
    assert unp_res == f"unpinned {fact_id}"

    # Update
    up_res = await svc.update_memory(
        fact_id=fact_id,
        summary="Updated workaround: busy_timeout=5000",
        kind="decision",
    )
    assert up_res == f"updated {fact_id}"

    updated_detail = await svc.get_memory(fact_id)
    assert "Updated workaround: busy_timeout=5000" in updated_detail
    assert "[decision]" in updated_detail

    # Forget
    fg_res = await svc.forget_memory(fact_id, reason="Issue resolved upstream in sqlean")
    assert fg_res == f"forgot {fact_id}: Issue resolved upstream in sqlean"

    # Verify soft-deleted fact is not found
    get_after = await svc.get_memory(fact_id)
    assert get_after == f"error: fact '{fact_id}' not found"


@pytest.mark.asyncio
async def test_memory_status_and_errors(service_setup: MemPlaneService) -> None:
    svc = service_setup

    status = await svc.memory_status()
    assert f"Project: {svc.project_id}" in status
    assert "Vector search: enabled" in status
    assert "Active facts:" in status

    # Invalid ref prefix never raises
    err_ref = await svc.get_memory("x_unknown_ref")
    assert err_ref == "error: invalid memory reference 'x_unknown_ref'"

    # Non-existent fact
    err_fact = await svc.get_memory("f_nonexistent99")
    assert err_fact == "error: fact 'f_nonexistent99' not found"
