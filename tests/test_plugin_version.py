"""Test that Claude Code plugin version equals Python package version (Section 14.2, M6.4)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_plugin_version_equals_package_version() -> None:
    pyproject_file = REPO_ROOT / "pyproject.toml"
    assert pyproject_file.exists(), "pyproject.toml not found"

    with open(pyproject_file, "rb") as f:
        pyproject_data = tomllib.load(f)
    package_version = pyproject_data["project"]["version"]

    plugin_file = REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    assert plugin_file.exists(), "plugin.json not found"

    with open(plugin_file, encoding="utf-8") as f:
        plugin_data = json.load(f)
    plugin_version = plugin_data["version"]

    assert plugin_version == package_version, (
        f"Version mismatch: plugin.json ({plugin_version}) != pyproject.toml ({package_version})"
    )


def test_skill_file_under_70_lines_and_covers_requirements() -> None:
    skill_file = REPO_ROOT / "plugin" / "skills" / "mem-plane" / "SKILL.md"
    assert skill_file.exists(), "SKILL.md not found"

    with open(skill_file, encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()

    assert len(lines) <= 70, f"SKILL.md is {len(lines)} lines; must be under 70 lines"
    assert "description: Use when you make a durable decision" in content
    assert "record_fact" in content
    assert "supersedes" in content
    assert "[STALE:" in content
    assert "stored as t_" in content
    assert "search_memory" in content
