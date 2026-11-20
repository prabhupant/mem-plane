# Claude Code Internal Formats & Payloads

This document records the exact, observed payload schemas and transcript data structures used by Claude Code (version `2.1.326`), captured via live sessions in `tests/fixtures/claude_code/`.

---

## 1. `PostToolUse` Hook Payload (`Bash`)

When a tool finishes execution, Claude Code invokes the `PostToolUse` hook with a JSON payload on `stdin`.

### Top-Level Payload Structure
```json
{
  "session_id": "string (UUID)",
  "transcript_path": "string (absolute path to session .jsonl)",
  "cwd": "string (working directory)",
  "prompt_id": "string (UUID)",
  "permission_mode": "string (e.g. 'default', 'bypassPermissions')",
  "effort": { "level": "string (e.g. 'medium')" },
  "hook_event_name": "PostToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "string (executed command line)",
    "description": "string (optional description)"
  },
  "tool_response": { ... },
  "tool_use_id": "string (e.g. 'toolu_01...')",
  "duration_ms": 6996
}
```

### Exact `tool_response` Shape for `Bash`

#### Standard / Small Output
When tool output is below the harness inline size limit:
```json
{
  "stdout": "hello",
  "stderr": "",
  "interrupted": false,
  "isImage": false,
  "noOutputExpected": false
}
```

#### Large Output (> 40 KB / > ~1,800 lines)
When tool output exceeds Claude Code's internal threshold, Claude Code truncates `stdout` in-memory and persists the complete raw output to disk:
```json
{
  "stdout": "output line 0\n... (truncated preview up to ~30,000 chars)",
  "stderr": "",
  "interrupted": false,
  "isImage": false,
  "noOutputExpected": false,
  "persistedOutputPath": "/home/user/.claude/projects/<slug>/<session-id>/tool-results/b183tumnu.txt",
  "persistedOutputSize": 41390
}
```

#### Keys Summary for `Bash.tool_response`:
| Key | Type | Description |
| :- | :- | :- |
| `stdout` | `str` | Standard output (truncated to ~30k chars if oversized). |
| `stderr` | `str` | Standard error stream. |
| `interrupted` | `bool` | True if the tool invocation was cancelled or interrupted. |
| `isImage` | `bool` | True if response contains image payload. |
| `noOutputExpected` | `bool` | True if command was executed asynchronously with no wait. |
| `persistedOutputPath` | `str \| None` | Path to full file when stdout exceeded internal threshold. |
| `persistedOutputSize` | `int \| None` | Total byte size of full unclipped output file. |

> **Critical Rule for Output Pruning (M3):**
> When returning `updatedToolOutput`, the hook **must preserve all existing keys** in `tool_response` (`interrupted`, `isImage`, `noOutputExpected`, etc.) and replace only `stdout` and `stderr`.

---

## 2. `SessionStart` Hook Payload

Triggered when a session begins or resumes.

```json
{
  "session_id": "string (UUID)",
  "transcript_path": "string (path)",
  "cwd": "string (path)",
  "hook_event_name": "SessionStart",
  "source": "startup | resume | compact | clear"
}
```

### `source` Values Seen:
1. `"startup"`: Initial cold start of a new interactive or headless session.
2. `"resume"`: Continuing an earlier session via `--resume <id>` or `-c`.
   - Additional fields sent: `seconds_since_last_response` (`int`), `context_tokens` (`int`), `prompt_cache_likely_expired` (`bool`), `estimated_cache_write_usd` (`float`).
3. `"compact"`: Fired immediately after compaction completes.
   - Additional fields sent: `model` (`str`, e.g. `"claude-opus-5-5"`), `prompt_id` (`str`).
4. `"clear"`: Fired when the user runs `/clear` to reset conversation context.

---

## 3. `PreCompact` Hook Payload

Triggered immediately prior to context compaction.

```json
{
  "session_id": "f399bd4b-fb86-4782-b173-b4561616c7e5",
  "transcript_path": "/home/user/.claude/projects/.../session.jsonl",
  "cwd": "/home/user/work/personal/mem-plane",
  "prompt_id": "7e429db2-2baa-4ab7-b117-abddff21503c",
  "hook_event_name": "PreCompact",
  "trigger": "manual",
  "custom_instructions": null
}
```

### `trigger` Values Seen:
* `"manual"`: User ran `/compact`. `custom_instructions` contains optional prompt instructions or `null`.
* `"auto"`: Automatic compaction triggered by the harness when token usage reaches context limits.

---

## 4. `Stop` and `SessionEnd` Hook Payloads

### `Stop`
Fired when the assistant turn completes:
```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string",
  "prompt_id": "string",
  "permission_mode": "string",
  "effort": { "level": "medium" },
  "hook_event_name": "Stop",
  "stop_hook_active": false,
  "last_assistant_message": "string (text of assistant message)",
  "background_tasks": [],
  "session_crons": []
}
```

### `SessionEnd`
Fired when session exits:
```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string",
  "prompt_id": "string",
  "hook_event_name": "SessionEnd",
  "reason": "other | exit | clear"
}
```

---

## 5. Transcript Structure (`.jsonl`)

Claude Code writes sessions as line-delimited JSON objects to `~/.claude/projects/<project-slug>/<session-id>.jsonl`.

### Observed Entry Types:
* `session-owner`: Account and organization metadata.
* `queue-operation`: User prompt enqueue and dequeue timestamps.
* `attachment`: System context attachments, initial git status snapshot, prompt snapshots.
* `user`: User prompt or tool result messages.
* `assistant`: Model response messages, tool use invocations, thinking blocks.
* `api-request`, `api-request-blob`, `api-request-shape`: Raw API turn tracking.
* `cost-state`: Real-time session token accounting.
* `last-prompt`: Checkpoint pointing to the current leaf UUID.
* `system:compact_boundary`: Marks context compaction events.
* `system:stop_hook_summary`: Summary of stop hook executions.
* `tools-used`: Aggregated tool usage summary.

### Where Message Content Lives
1. **User messages:**
   - `entry["type"] == "user"`
   - Text prompts: `entry["message"]["content"]` is a `str` (or list of dicts with `{"type": "text", "text": "..."}`).
   - Tool results: `entry["message"]["content"]` is a list of dicts with `{"type": "tool_result", "content": "...", "is_error": bool, "tool_use_id": "..."}`.

2. **Assistant messages:**
   - `entry["type"] == "assistant"`
   - Blocks live in `entry["message"]["content"]`:
     - Text responses: `{"type": "text", "text": "..."}`
     - Tool calls: `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`

### Where Usage Data Lives
In assistant records (`entry["type"] == "assistant"`), under `entry["message"]["usage"]`:
```json
{
  "input_tokens": 2,
  "cache_creation_input_tokens": 20938,
  "cache_read_input_tokens": 11832,
  "output_tokens": 104,
  "output_tokens_details": {
    "thinking_tokens": 0
  }
}
```

### How Compaction Appears in Transcript
Compaction is denoted by an entry with:
- `entry["type"] == "system"`
- `entry["subtype"] == "compact_boundary"`
- `entry["compactMetadata"]`:
  ```json
  {
    "trigger": "manual",
    "preTokens": 34231,
    "durationMs": 12924,
    "preservedSegment": {
      "headUuid": "bb122bfd-ff6b-47ea-a590-f2d941cb0ec4",
      "anchorUuid": "6308d352-7dd5-448b-bcb5-2031e28be5d5",
      "tailUuid": "22584a99-42c3-4896-b3c0-6836c18dcdb7"
    },
    "postTokens": 9452,
    "cumulativeDroppedTokens": 24779
  }
  ```
All dialogue preceding `compact_boundary` remains physically present in the `.jsonl` transcript file, but is excluded from active model context on subsequent turns.
