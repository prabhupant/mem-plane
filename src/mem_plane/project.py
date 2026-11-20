"""Project identity resolution and remote URL normalization."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectInfo:
    project_id: str
    display_name: str
    root_path: str
    remote_url: str | None


_PROJECT_CACHE: dict[str, ProjectInfo] = {}


def normalize_remote_url(raw_url: str) -> str:
    """Normalize git remote URLs across protocols, credentials, and suffixes.

    Examples:
        git@github.com:owner/repo.git -> github.com/owner/repo
        https://token@github.com/owner/repo.git -> github.com/owner/repo
        ssh://git@gitlab.com/owner/repo -> gitlab.com/owner/repo
    """
    url = raw_url.strip()
    if not url:
        return ""

    # Strip protocol prefix (ssh://, https://, http://, git://)
    url = re.sub(r"^(?:https?|git|ssh)://", "", url)

    # Strip user credentials (e.g. user:token@ or git@)
    url = re.sub(r"^[^@/]+@", "", url)

    # Handle SCP-like git syntax: host.com:owner/repo -> host.com/owner/repo
    if ":" in url and "/" in url:
        # Avoid breaking port numbers like host:8080/repo
        parts = url.split(":", 1)
        if not re.match(r"^\d+", parts[1]):
            url = f"{parts[0]}/{parts[1]}"

    # Lowercase host part
    if "/" in url:
        host, path = url.split("/", 1)
        url = f"{host.lower()}/{path}"
    else:
        url = url.lower()

    # Strip trailing .git and trailing slash
    url = re.sub(r"\.git/?$", "", url)
    url = url.rstrip("/")
    return url


def _hash_id(value: str) -> str:
    return "p_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def resolve_project(cwd: str | None = None) -> ProjectInfo:
    """Resolve project identity for a given working directory.

    Order:
      1. Check MEM_PLANE_PROJECT_ID environment variable override.
      2. Git top-level directory (`git rev-parse --show-toplevel`).
      3. Remote origin normalization -> `p_<sha1[:16]>`.
      4. Fallback to git root path -> `p_<sha1[:16]>`.
      5. Fallback to cwd -> `p_<sha1[:16]>`.
    """
    override_id = os.environ.get("MEM_PLANE_PROJECT_ID")

    base_dir = cwd
    if not base_dir:
        base_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not base_dir:
        base_dir = os.getcwd()
    abs_base = os.path.abspath(base_dir)

    # Check process cache (unless override is set)
    cache_key = f"{override_id or ''}::{abs_base}"
    if cache_key in _PROJECT_CACHE:
        return _PROJECT_CACHE[cache_key]

    root_path = abs_base
    remote_url: str | None = None

    # Check git repo root
    try:
        proc = subprocess.run(
            ["git", "-C", abs_base, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            root_path = os.path.abspath(proc.stdout.strip())
            # Check remote origin
            proc_remote = subprocess.run(
                ["git", "-C", root_path, "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc_remote.returncode == 0 and proc_remote.stdout.strip():
                remote_url = proc_remote.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        remote_url = None

    display_name = os.path.basename(root_path) or "root"

    if override_id:
        project_id = override_id.strip()
    elif remote_url:
        normalized = normalize_remote_url(remote_url)
        project_id = _hash_id(normalized)
    else:
        project_id = _hash_id(root_path)

    info = ProjectInfo(
        project_id=project_id,
        display_name=display_name,
        root_path=root_path,
        remote_url=normalize_remote_url(remote_url) if remote_url else None,
    )
    _PROJECT_CACHE[cache_key] = info
    return info
