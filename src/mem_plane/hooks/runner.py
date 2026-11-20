"""Universal entry point for Claude Code hook handlers (Section 9.1)."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from mem_plane.config import Settings


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
    """Execute a Claude Code hook handler.

    Guarantees:
    - Never raises (wrapped in try/except BaseException)
    - Always exits 0
    - Prints exactly one JSON object or nothing
    - Logs errors only to ~/.mem-plane/logs/hooks.log
    """
    try:
        raw_payload = sys.stdin.read()

        if os.environ.get("MEM_PLANE_HOOK_DEBUG") == "1":
            _save_debug_payload(event, raw_payload)

        if not raw_payload.strip():
            return 0

        try:
            data = json.loads(raw_payload)
            if not isinstance(data, dict):
                return 0
        except (json.JSONDecodeError, ValueError, TypeError):
            # Malformed JSON payload; exit 0 with no output
            return 0

        settings = Settings()
        normalized_event = event.lower().replace("-", "").replace("_", "")

        result: dict[str, Any] | None = None

        if normalized_event in ("posttooluse", "posttool"):
            from mem_plane.hooks.inputs import PostToolUseInput
            from mem_plane.hooks.post_tool_use import handle_post_tool_use

            payload_obj = PostToolUseInput.model_validate(data)
            result = handle_post_tool_use(payload_obj, settings)

        elif normalized_event == "stop":
            from mem_plane.hooks.inputs import StopInput
            from mem_plane.hooks.stop import handle_stop

            stop_payload = StopInput.model_validate(data)
            result = handle_stop(stop_payload, settings)

        elif normalized_event in ("precompact", "compact"):
            from mem_plane.hooks.inputs import PreCompactInput
            from mem_plane.hooks.pre_compact import handle_pre_compact

            pre_compact_payload = PreCompactInput.model_validate(data)
            result = handle_pre_compact(pre_compact_payload, settings)

        elif normalized_event in ("sessionend", "end"):
            from mem_plane.hooks.inputs import SessionEndInput
            from mem_plane.hooks.session_end import handle_session_end

            end_payload = SessionEndInput.model_validate(data)
            result = handle_session_end(end_payload, settings)

        elif normalized_event in ("sessionstart", "start"):
            from mem_plane.hooks.inputs import SessionStartInput
            from mem_plane.hooks.session_start import handle_session_start

            start_payload = SessionStartInput.model_validate(data)
            result = handle_session_start(start_payload, settings)

        elif normalized_event in ("userpromptsubmit", "promptsubmit", "userprompt"):
            from mem_plane.hooks.inputs import UserPromptSubmitInput
            from mem_plane.hooks.user_prompt_submit import handle_user_prompt_submit

            user_prompt_payload = UserPromptSubmitInput.model_validate(data)
            result = handle_user_prompt_submit(user_prompt_payload, settings)

        if result is not None:
            sys.stdout.write(json.dumps(result))
            sys.stdout.flush()

        return 0

    except BaseException as exc:  # noqa: BLE001
        _log_error(f"Unhandled error in hook {event}: {exc}\n{traceback.format_exc()}")
        return 0
