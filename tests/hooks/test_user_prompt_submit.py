"""Unit tests for UserPromptSubmit hook handler (Section 9.8, M7.1)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from mem_plane.config import Settings
from mem_plane.core.models import Fact, FactKind
from mem_plane.hooks.inputs import UserPromptSubmitInput
from mem_plane.hooks.runner import run_hook
from mem_plane.hooks.user_prompt_submit import handle_user_prompt_submit
from mem_plane.storage.sqlite.store import SQLiteStore


def test_user_prompt_submit_disabled_by_default(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "memory.db"),
        auto_recall=False,
    )
    payload = UserPromptSubmitInput(
        session_id="s1",
        cwd=str(tmp_path),
        prompt="How do we handle discounts in orders?",
    )
    result = handle_user_prompt_submit(payload, settings)
    assert result is None


def test_user_prompt_submit_empty_prompt(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "memory.db"),
        auto_recall=True,
    )
    payload = UserPromptSubmitInput(
        session_id="s1",
        cwd=str(tmp_path),
        prompt="   ",
    )
    result = handle_user_prompt_submit(payload, settings)
    assert result is None


def test_user_prompt_submit_matching_and_threshold(tmp_path: Path) -> None:
    db_file = tmp_path / "memory.db"
    settings = Settings(
        db_path=str(db_file),
        auto_recall=True,
        auto_recall_min_score=5.0,
    )

    store = SQLiteStore(database=str(db_file), dimension=384, settings=settings)
    try:
        from mem_plane.project import resolve_project

        proj = resolve_project(str(tmp_path))
        store.upsert_project(proj)

        now = datetime.now(UTC)
        # Populate corpus so FTS5 IDF has background statistics
        for i in range(25):
            store.create_fact(
                Fact(
                    fact_id=f"f_bg_{i:04d}",
                    project_id=proj.project_id,
                    kind=FactKind.NOTE,
                    summary=f"General operational note number {i}",
                    body=f"Unrelated context documentation body text {i}.",
                    created_at=now,
                    updated_at=now,
                )
            )

        fact = Fact(
            fact_id="f_000000000001",
            project_id=proj.project_id,
            kind=FactKind.RULE,
            summary="All currency values must be integers in cents",
            body="Never use floating point numbers for currency values to prevent rounding errors.",
            created_at=now,
            updated_at=now,
        )
        store.create_fact(fact)
    finally:
        store.close()

    payload_match = UserPromptSubmitInput(
        session_id="s1",
        cwd=str(tmp_path),
        prompt="currency values integers cents",
    )
    result = handle_user_prompt_submit(payload_match, settings)
    assert result is not None
    assert "hookSpecificOutput" in result
    add_ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "mem-plane: possibly relevant memories:" in add_ctx
    assert "f_000000000001" in add_ctx
    assert "currency values must be integers in cents" in add_ctx
    assert len(add_ctx) <= 800

    # Test high threshold drops match
    high_threshold_settings = Settings(
        db_path=str(db_file),
        auto_recall=True,
        auto_recall_min_score=100.0,
    )
    result_high = handle_user_prompt_submit(payload_match, high_threshold_settings)
    assert result_high is None


def test_user_prompt_submit_via_hook_runner(tmp_path: Path) -> None:
    db_file = tmp_path / "memory.db"
    settings = Settings(
        db_path=str(db_file),
        auto_recall=True,
        auto_recall_min_score=5.0,
    )
    store = SQLiteStore(database=str(db_file), dimension=384, settings=settings)
    try:
        from mem_plane.project import resolve_project

        proj = resolve_project(str(tmp_path))
        store.upsert_project(proj)

        now = datetime.now(UTC)
        for i in range(25):
            store.create_fact(
                Fact(
                    fact_id=f"f_bg_run_{i:04d}",
                    project_id=proj.project_id,
                    kind=FactKind.NOTE,
                    summary=f"General system background log entry {i}",
                    body=f"Miscellaneous operational context {i}.",
                    created_at=now,
                    updated_at=now,
                )
            )

        fact = Fact(
            fact_id="f_000000000002",
            project_id=proj.project_id,
            kind=FactKind.DECISION,
            summary="PostgreSQL is the single datastore",
            body="Redis was removed in September 2026.",
            created_at=now,
            updated_at=now,
        )
        store.create_fact(fact)
    finally:
        store.close()

    raw_json = json.dumps(
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "prompt": "PostgreSQL single datastore architecture",
        }
    )

    with (
        patch("sys.stdin", io.StringIO(raw_json)),
        patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        patch("mem_plane.hooks.runner.Settings", return_value=settings),
    ):
        code = run_hook("user-prompt-submit")
        assert code == 0
        output = mock_stdout.getvalue()
        assert output.strip() != ""
        parsed = json.loads(output)
        assert "hookSpecificOutput" in parsed
        assert "f_000000000002" in parsed["hookSpecificOutput"]["additionalContext"]


def test_user_prompt_submit_does_not_import_heavy_modules() -> None:
    code = (
        "import io, sys\n"
        "from unittest.mock import patch\n"
        "from mem_plane.hooks.runner import run_hook\n"
        'payload = \'{"session_id":"s1","cwd":".","prompt":"test prompt"}\'\n'
        "with patch('sys.stdin', io.StringIO(payload)):\n"
        "    run_hook('user-prompt-submit')\n"
        "assert 'openai' not in sys.modules, 'openai imported during user-prompt-submit!'\n"
        "assert 'fastembed' not in sys.modules, 'fastembed imported during user-prompt-submit!'\n"
        "assert 'numpy' not in sys.modules, 'numpy imported during user-prompt-submit!'\n"
        "assert 'mcp' not in sys.modules, 'mcp imported during user-prompt-submit!'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Failed: {res.stderr}"
