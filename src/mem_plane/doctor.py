"""Diagnostic self-test and hook configuration printer for mem-plane (Section 10)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

from mem_plane.config import Settings

HOOKS_CONFIG: dict[str, Any] = {
    "hooks": {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {"type": "command", "command": "mem-plane", "args": ["hook", "session-start"]}
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "mem-plane", "args": ["hook", "post-tool-use"]}
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "mem-plane",
                        "args": ["hook", "stop"],
                        "async": True,
                    }
                ]
            }
        ],
        "PreCompact": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "mem-plane",
                        "args": ["hook", "pre-compact"],
                        "timeout": 30,
                    }
                ]
            }
        ],
        "SessionEnd": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "mem-plane",
                        "args": ["hook", "session-end"],
                        "async": True,
                    }
                ]
            }
        ],
    }
}


def print_hooks_config() -> None:
    """Print standard Claude Code hooks configuration JSON."""
    print(json.dumps(HOOKS_CONFIG, indent=2))


def run_doctor(settings: Settings | None = None) -> int:
    """Run all diagnostic checks for mem-plane installation."""
    cfg = settings or Settings()
    issues: list[str] = []

    print("=" * 60)
    print("MEM-PLANE DIAGNOSTIC CHECK")
    print("=" * 60)

    # 1. Python version check (>= 3.10)
    py_ver = sys.version_info
    py_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    if py_ver >= (3, 10):
        print(f"[PASS] Python version: {py_str}")
    else:
        print(f"[FAIL] Python version: {py_str} (requires >= 3.10)")
        issues.append("Upgrade Python to version 3.10 or newer.")

    # 2. sqlite-vec check
    vec_ok = False
    try:
        from mem_plane.storage.sqlite.connection import create_sync_connection
        from mem_plane.storage.sqlite.schema import supports_vec_partition_keys

        conn = create_sync_connection(":memory:", load_vec=True)
        try:
            cur = conn.execute("SELECT vec_version();")
            ver_str = cur.fetchone()[0]
            part_keys = supports_vec_partition_keys(conn)
            print(f"[PASS] sqlite-vec: loaded version {ver_str} (partition keys: {part_keys})")
            vec_ok = True
        finally:
            conn.close()
    except (sqlite3.Error, ImportError, RuntimeError) as e:
        print(f"[FAIL] sqlite-vec: could not load ({e})")
        issues.append("Install or repair sqlite-vec: pip install 'sqlite-vec>=0.1.6'")

    # 3. Embedding provider check
    if cfg.embedding_provider == "openai":
        try:
            import openai  # noqa: F401

            print("[PASS] openai: installed and importable")
        except ImportError:
            print("[FAIL] openai: not installed (install with pip install openai)")
            issues.append("Install openai for vector embeddings: pip install 'openai>=1.0.0'")

        api_key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if api_key:
            masked = api_key[:3] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
            print(f"[PASS] OPENAI_API_KEY: configured ({masked})")
        else:
            print("[WARN] OPENAI_API_KEY: not set (vector search falls back to keyword only)")
            issues.append("Set OPENAI_API_KEY environment variable for OpenAI vector embeddings.")
    elif cfg.embedding_provider == "fastembed":
        try:
            import fastembed  # noqa: F401

            print("[PASS] fastembed: installed and importable")
        except ImportError:
            print("[WARN] fastembed: not installed (vector search falls back to keyword only)")
            issues.append(
                "Install fastembed for dense vector search: pip install 'mem-plane[embed]'"
            )
    elif cfg.embedding_provider == "dummy":
        print("[INFO] Embedding provider is set to 'dummy' (offline/test mode)")
    else:
        print(f"[INFO] Embedding provider is set to '{cfg.embedding_provider}'")

    # 4. Database path and permissions
    db_path = Path(cfg.db_path).expanduser().resolve()
    parent_dir = db_path.parent
    if parent_dir.exists():
        mode = stat.S_IMODE(parent_dir.stat().st_mode)
        if mode == 0o700:
            print(f"[PASS] Database directory permissions: {oct(mode)} ({parent_dir})")
        else:
            print(f"[WARN] Database directory permissions: {oct(mode)} (expected 0700)")
            issues.append(f"Fix directory permissions: chmod 0700 {parent_dir}")
    else:
        print(f"[INFO] Database directory will be created at: {parent_dir}")

    if db_path.exists():
        mode = stat.S_IMODE(db_path.stat().st_mode)
        if mode == 0o600:
            print(f"[PASS] Database file permissions: {oct(mode)} ({db_path})")
        else:
            print(f"[WARN] Database file permissions: {oct(mode)} (expected 0600)")
            issues.append(f"Fix database file permissions: chmod 0600 {db_path}")

    # 5. mem-plane executable on PATH
    exe_path = shutil.which("mem-plane")
    if exe_path:
        print(f"[PASS] Executable on PATH: {exe_path}")
    else:
        print("[WARN] 'mem-plane' not found on PATH")
        issues.append(
            "Ensure mem-plane is installed in a PATH directory (e.g. uv tool install mem-plane)."
        )

    # 6. Check hook configuration in Claude settings
    settings_paths = [
        Path.cwd() / ".claude" / "settings.json",
        Path.home() / ".claude" / "settings.json",
    ]
    hooks_found = False
    for sp in settings_paths:
        if sp.is_file():
            try:
                with open(sp, encoding="utf-8") as f:
                    data = json.load(f)
                if "hooks" in data:
                    raw_str = json.dumps(data["hooks"])
                    if "mem-plane" in raw_str:
                        hooks_found = True
                        print(f"[PASS] Hooks configured in: {sp}")
                        break
            except (json.JSONDecodeError, OSError):
                pass

    if not hooks_found:
        print("[INFO] No hooks configured in local or global .claude/settings.json")
        print("       Run 'mem-plane doctor --print-hooks' to see recommended hook configuration.")

    print("=" * 60)
    if not issues:
        print("STATUS: All essential checks passed!")
        print("=" * 60)
        return 0

    print("STATUS: Issues detected. Suggested fixes:")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")
    print("=" * 60)
    return 1 if not vec_ok else 0
