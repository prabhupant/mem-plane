"""Minimal hook runner for Claude Code hooks."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import UTC, datetime


def _get_log_dir() -> str:
    log_dir = os.environ.get("MEM_PLANE_LOG_DIR")
    if log_dir:
        return os.path.expanduser(log_dir)
    return os.path.expanduser("~/.mem-plane/logs")


def _log_error(message: str) -> None:
    try:
        log_dir = _get_log_dir()
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        log_file = os.path.join(log_dir, "hooks.log")
        timestamp = datetime.now(UTC).isoformat()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except BaseException:  # noqa: BLE001, S110
        pass


def _save_debug_payload(event: str, raw_payload: str) -> None:
    try:
        log_dir = _get_log_dir()
        payload_dir = os.path.join(log_dir, "payloads")
        os.makedirs(payload_dir, mode=0o700, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        payload_file = os.path.join(payload_dir, f"{event}-{timestamp}.json")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(payload_file, flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw_payload)
    except BaseException as exc:  # noqa: BLE001
        _log_error(f"Failed to save debug payload for event {event}: {exc}")


def run_hook(event: str) -> int:
    """Entry point for executing a Claude Code hook event.

    Never raises, never blocks, and prints nothing unless a specific
    handler produces valid JSON output. Always returns 0.
    """
    try:
        raw_payload = sys.stdin.read()

        if os.environ.get("MEM_PLANE_HOOK_DEBUG") == "1":
            _save_debug_payload(event, raw_payload)

        # In M0, the runner is in debug capture mode only.
        # Specific event handlers are wired up in M3, M4, M5, M7.
        return 0
    except BaseException as exc:  # noqa: BLE001
        _log_error(f"Unhandled error in hook {event}: {exc}\n{traceback.format_exc()}")
        return 0
