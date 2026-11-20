"""Tests for the Claude Code hook runner."""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import patch

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
        "assert 'fastembed' not in sys.modules, 'fastembed imported!'; "
        "assert 'numpy' not in sys.modules, 'numpy imported!'; "
        "assert 'mcp' not in sys.modules, 'mcp imported!'"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Failed: {res.stderr}"
