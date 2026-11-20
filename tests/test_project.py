"""Tests for project identity resolution and remote URL normalization."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

from mem_plane.project import normalize_remote_url, resolve_project


def test_normalize_remote_url_ssh() -> None:
    url = "git@github.com:example/repo.git"
    assert normalize_remote_url(url) == "github.com/example/repo"


def test_normalize_remote_url_https() -> None:
    url = "https://github.com/example/repo.git"
    assert normalize_remote_url(url) == "github.com/example/repo"


def test_normalize_remote_url_credentials() -> None:
    url = "https://user:secret-token@gitlab.com/group/subgroup/project.git/"
    assert normalize_remote_url(url) == "gitlab.com/group/subgroup/project"


def test_normalize_remote_url_ssh_protocol() -> None:
    url = "ssh://git@bitbucket.org:22/team/repo.git"
    assert normalize_remote_url(url) == "bitbucket.org:22/team/repo"


def test_resolve_project_override() -> None:
    with patch.dict(os.environ, {"MEM_PLANE_PROJECT_ID": "p_custom1234"}):
        info = resolve_project()
        assert info.project_id == "p_custom1234"


def test_resolve_project_git_with_remote(tmp_path: object) -> None:
    repo = str(tmp_path)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "remote", "add", "origin", "git@github.com:org/test-repo.git"],
        check=True,
        capture_output=True,
    )

    info = resolve_project(repo)
    assert info.project_id.startswith("p_")
    assert len(info.project_id) == 18  # "p_" + 16 chars
    assert info.remote_url == "github.com/org/test-repo"
    assert info.root_path == repo


def test_resolve_project_git_no_remote(tmp_path: object) -> None:
    repo = str(tmp_path)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)

    info = resolve_project(repo)
    assert info.project_id.startswith("p_")
    assert info.remote_url is None
    assert info.root_path == repo


def test_resolve_project_non_git(tmp_path: object) -> None:
    non_git_dir = str(tmp_path)
    info = resolve_project(non_git_dir)
    assert info.project_id.startswith("p_")
    assert info.remote_url is None
    assert info.root_path == non_git_dir


def test_resolve_project_caching(tmp_path: object) -> None:
    target = str(tmp_path)
    info1 = resolve_project(target)
    info2 = resolve_project(target)
    assert info1 is info2
