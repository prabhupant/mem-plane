"""Unit tests for PostToolUse pruning handler (Sections 9.1, 9.2, 15.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mem_plane.config import Settings
from mem_plane.hooks.inputs import PostToolUseInput
from mem_plane.hooks.post_tool_use import handle_post_tool_use
from mem_plane.storage.sqlite.store import SQLiteStore

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "claude_code"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "hooks_test.db"),
        prune_enabled=True,
        prune_max_lines=120,
        prune_max_chars=8000,
        preview_max_chars=4000,
    )


def test_post_tool_use_small_output_below_thresholds(test_settings: Settings) -> None:
    payload = PostToolUseInput(
        session_id="s_test001",
        cwd=".",
        tool_name="Bash",
        tool_input={"command": "echo 'small output'"},
        tool_response={
            "stdout": "small output\n",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
        },
    )
    result = handle_post_tool_use(payload, test_settings)
    assert result is None


def test_post_tool_use_non_bash_ignored(test_settings: Settings) -> None:
    # Read tool should never be pruned
    payload = PostToolUseInput(
        session_id="s_test002",
        cwd=".",
        tool_name="Read",
        tool_input={"file_path": "big.txt"},
        tool_response={"stdout": "line\n" * 500, "stderr": ""},
    )
    result = handle_post_tool_use(payload, test_settings)
    assert result is None


def test_post_tool_use_unknown_shape_ignored(test_settings: Settings) -> None:
    # Malformed or unrecognized tool_response
    payload1 = PostToolUseInput(
        session_id="s_test003",
        cwd=".",
        tool_name="Bash",
        tool_input={"command": "ls"},
        tool_response="string_response_not_dict",
    )
    assert handle_post_tool_use(payload1, test_settings) is None

    payload2 = PostToolUseInput(
        session_id="s_test003",
        cwd=".",
        tool_name="Bash",
        tool_input={"command": "ls"},
        tool_response={"some_unexpected_key": 123},
    )
    assert handle_post_tool_use(payload2, test_settings) is None


def test_post_tool_use_exclusion_regex(test_settings: Settings) -> None:
    large_stdout = "diff line\n" * 300
    for cmd in [
        "cat file.py",
        "head -n 200 file.py",
        "tail -f log.txt",
        "git diff",
        "git show HEAD",
        "mem-plane status",
    ]:
        payload = PostToolUseInput(
            session_id="s_test004",
            cwd=".",
            tool_name="Bash",
            tool_input={"command": cmd},
            tool_response={"stdout": large_stdout, "stderr": ""},
        )
        assert handle_post_tool_use(payload, test_settings) is None


def test_post_tool_use_large_output_pruned_and_trace_saved(test_settings: Settings) -> None:
    lines = [f"setup step {i}" for i in range(100)]
    lines.append("test_feature_alpha ... AssertionError: expected 200 got 500")
    lines.extend([f"teardown step {i}" for i in range(100)])
    large_stdout = "\n".join(lines)

    original_resp = {
        "stdout": large_stdout,
        "stderr": "warning: deprecated flag used\n",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
        "exitCode": 1,
        "custom_harness_meta": {"tag": "pytest"},
    }

    payload = PostToolUseInput(
        session_id="s_test005",
        cwd=".",
        tool_name="Bash",
        tool_input={"command": "pytest -v"},
        tool_response=original_resp,
    )

    result = handle_post_tool_use(payload, test_settings)
    assert result is not None
    assert "hookSpecificOutput" in result
    hso = result["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    updated = hso["updatedToolOutput"]

    # Verify EVERY original key is preserved in the replacement
    for key in original_resp:
        assert key in updated, f"Missing key in replacement: {key}"

    # Metadata keys untouched
    assert updated["interrupted"] is False
    assert updated["exitCode"] == 1
    assert updated["custom_harness_meta"] == {"tag": "pytest"}

    # stdout shortened
    new_stdout = updated["stdout"]
    assert len(new_stdout) < len(large_stdout)
    assert "[mem-plane: output shortened." in new_stdout
    assert "stored as t_" in new_stdout
    assert "Exit code 1." in new_stdout
    assert "AssertionError" in new_stdout

    # stderr was below half threshold (1 line), so it was kept intact
    assert updated["stderr"] == "warning: deprecated flag used\n"

    # Extract trace ID from banner
    banner_match = json.loads(json.dumps(new_stdout))
    import re

    m = re.search(r"stored as (t_[0-9a-f]{10})", banner_match)
    assert m is not None
    trace_id = m.group(1)

    # Verify trace is saved in database
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    try:
        from mem_plane.project import resolve_project

        proj = resolve_project(".")
        trace = store.get_trace(trace_id, proj.project_id)
        assert trace is not None
        assert trace.exit_code == 1
        assert trace.command == "pytest -v"
        assert trace.stdout == large_stdout
    finally:
        store.close()


def test_post_tool_use_with_m0_fixture_payload(test_settings: Settings) -> None:
    fixture_file = FIXTURES_DIR / "post_tool_use_large.json"
    if not fixture_file.exists():
        pytest.skip(f"Fixture {fixture_file} not found")

    with open(fixture_file, encoding="utf-8") as f:
        data = json.load(f)

    payload = PostToolUseInput.model_validate(data)
    result = handle_post_tool_use(payload, test_settings)

    assert result is not None
    updated = result["hookSpecificOutput"]["updatedToolOutput"]

    # Original keys preserved
    for key in data["tool_response"]:
        assert key in updated

    assert "[mem-plane: output shortened." in updated["stdout"]


@pytest.mark.asyncio
async def test_post_tool_use_and_get_memory_grep(test_settings: Settings) -> None:
    """M3.10 verification: output shortened, trace stored, get_memory grep retrieves line."""
    lines = [f"compilation line {i}" for i in range(150)]
    lines.append("CRITICAL_SYNTAX_ERROR: unexpected token on line 42")
    lines.extend([f"footer line {i}" for i in range(50)])
    large_stdout = "\n".join(lines)

    payload = PostToolUseInput(
        session_id="s_m3_10",
        cwd=".",
        tool_name="Bash",
        tool_input={"command": "make build"},
        tool_response={
            "stdout": large_stdout,
            "stderr": "",
            "exitCode": 2,
        },
    )

    result = handle_post_tool_use(payload, test_settings)
    assert result is not None
    updated_stdout = result["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "[mem-plane: output shortened." in updated_stdout

    import re

    m = re.search(r"stored as (t_[0-9a-f]{10})", updated_stdout)
    assert m is not None
    trace_id = m.group(1)

    # Now verify get_memory with grep on MemPlaneService
    from mem_plane.project import resolve_project
    from mem_plane.server.service import MemPlaneService

    project = resolve_project(".")
    store = SQLiteStore(database=test_settings.db_path, load_vec=False)
    service = MemPlaneService(
        store=store,
        project_id=project.project_id,
        settings=test_settings,
    )
    try:
        ret = await service.get_memory(ref=trace_id, grep="CRITICAL_SYNTAX_ERROR")
        assert "CRITICAL_SYNTAX_ERROR: unexpected token on line 42" in ret
        assert ">   151 | CRITICAL_SYNTAX_ERROR" in ret
    finally:
        store.close()
