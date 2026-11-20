"""Tests for the Claude Code hook runner."""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import patch

import pytest

from mem_plane.hooks.runner import run_hook


def test_hook_runner_success_empty_stdout(capsys: object) -> None:
    with patch("sys.stdin", io.StringIO('{"test": "payload"}')):
        exit_code = run_hook("stop")
    assert exit_code == 0


def test_hook_runner_malformed_input() -> None:
    with patch("sys.stdin", io.StringIO("not valid json at all")):
        exit_code = run_hook("session-start")
    assert exit_code == 0


def test_hook_runner_debug_payload_saved(tmp_path: object) -> None:
    log_dir = str(tmp_path)
    with (
        patch.dict(os.environ, {"MEM_PLANE_HOOK_DEBUG": "1", "MEM_PLANE_LOG_DIR": log_dir}),
        patch("sys.stdin", io.StringIO('{"event": "test"}')),
    ):
        exit_code = run_hook("post-tool-use")

    assert exit_code == 0
    payload_dir = os.path.join(log_dir, "payloads")
    assert os.path.exists(payload_dir)
    files = os.listdir(payload_dir)
    assert len(files) == 1
    assert files[0].startswith("post-tool-use-")

    filepath = os.path.join(payload_dir, files[0])
    mode = os.stat(filepath).st_mode & 0o777
    assert mode == 0o600
    with open(filepath, encoding="utf-8") as f:
        assert f.read() == '{"event": "test"}'


def test_hook_runner_does_not_import_heavy_modules() -> None:
    import subprocess

    code = (
        "import sys, mem_plane.hooks.runner; "
        "assert 'openai' not in sys.modules, 'openai imported!'; "
        "assert 'fastembed' not in sys.modules, 'fastembed imported!'; "
        "assert 'numpy' not in sys.modules, 'numpy imported!'; "
        "assert 'mcp' not in sys.modules, 'mcp imported!'"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Failed: {res.stderr}"


def test_hook_execution_does_not_import_heavy_modules() -> None:
    import subprocess

    code = (
        "import io, sys\n"
        "from unittest.mock import patch\n"
        "from mem_plane.hooks.runner import run_hook\n"
        'payload = \'{"session_id":"s1","cwd":".","hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"echo hi"},"tool_response":{"stdout":"hi","stderr":""}}\'\n'
        "with patch('sys.stdin', io.StringIO(payload)):\n"
        "    run_hook('post-tool-use')\n"
        "assert 'openai' not in sys.modules, 'openai imported during hook execution!'\n"
        "assert 'fastembed' not in sys.modules, 'fastembed imported during hook execution!'\n"
        "assert 'numpy' not in sys.modules, 'numpy imported during hook execution!'\n"
        "assert 'mcp' not in sys.modules, 'mcp imported during hook execution!'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Failed: {res.stderr}"


def test_every_fixture_payload_runs_through_debug_runner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import glob
    import json
    from pathlib import Path

    fixtures_dir = Path(__file__).parent.parent / "fixtures" / "claude_code"
    json_files = glob.glob(str(fixtures_dir / "*.json"))
    assert len(json_files) >= 5, "Expected at least 5 fixture payloads"

    for json_file in json_files:
        with open(json_file, encoding="utf-8") as f:
            content = f.read()

        data = json.loads(content)
        event_name = data.get("hook_event_name", "unknown")
        # Map event_name from PascalCase to kebab-case
        event_kebab = {
            "SessionStart": "session-start",
            "PostToolUse": "post-tool-use",
            "Stop": "stop",
            "PreCompact": "pre-compact",
            "SessionEnd": "session-end",
            "UserPromptSubmit": "user-prompt-submit",
        }.get(event_name, "stop")

        with patch("sys.stdin", io.StringIO(content)):
            exit_code = run_hook(event_kebab)

        captured = capsys.readouterr()
        assert exit_code == 0, f"Failed for {json_file}"
        if captured.out:
            # Must be valid single JSON object
            parsed = json.loads(captured.out)
            assert isinstance(parsed, dict)
            assert "hookSpecificOutput" in parsed
