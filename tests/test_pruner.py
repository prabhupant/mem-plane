"""Unit and integration tests for EphemeralToolPruner."""

import pytest

from mem_plane.core.pruner import EphemeralToolPruner
from mem_plane.storage.sqlite.backend import SQLiteBackend


def test_pruner_short_output_passthrough() -> None:
    """Short tool outputs below line and character thresholds should not be pruned."""
    pruner = EphemeralToolPruner(max_lines=15, max_chars=600)
    short_output = "Build succeeded.\n0 errors, 0 warnings."

    assert not pruner.should_prune(short_output)


@pytest.mark.asyncio
async def test_pruner_short_output_async_passthrough() -> None:
    pruner = EphemeralToolPruner(max_lines=15, max_chars=600)
    short_output = "Line 1\nLine 2\nLine 3"

    processed, trace_id = await pruner.process_tool_output(short_output)
    assert processed == short_output
    assert trace_id is None


@pytest.mark.asyncio
async def test_pruner_200_line_stack_trace_truncation() -> None:
    """A 200-line stack trace must be truncated with head/tail preview and trace_id banner."""
    backend = SQLiteBackend(database=":memory:", dimension=384)
    await backend.initialize()
    try:
        pruner = EphemeralToolPruner(storage=backend, max_lines=15, max_chars=600)

        # Generate 200-line mock stack trace
        lines = [f"Trace line {i}: function_call_at_step_{i}()" for i in range(1, 201)]
        lines[0] = "Traceback (most recent call last):"
        lines[-1] = "RuntimeError: Out of memory in cuda allocator"
        raw_trace = "\n".join(lines)

        processed, trace_id = await pruner.process_tool_output(raw_trace)

        # 1. Verify trace_id is generated
        assert trace_id is not None
        assert len(trace_id) > 0

        # 2. Verify banner format
        expected_banner_part = f"[TOOL OUTPUT TRUNCATED: 200 lines. Trace ID: {trace_id}."
        assert expected_banner_part in processed
        assert f"Call dereference_raw_trace('{trace_id}') for full trace.]" in processed

        # 3. Verify head and tail slices
        assert "Traceback (most recent call last):" in processed
        assert "RuntimeError: Out of memory in cuda allocator" in processed
        assert "... [truncated] ..." in processed

        # Verify preview line count is small
        preview_lines = processed.splitlines()
        # banner (1) + 5 head + 1 truncated marker + 5 tail = ~12-13 lines
        assert len(preview_lines) < 20

        # 4. Verify raw trace is retrievable out-of-band by trace_id
        retrieved_raw = await backend.get_tool_trace(trace_id)
        assert retrieved_raw == raw_trace
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_pruner_character_threshold_trigger() -> None:
    """An output with few lines but excessive characters must be truncated."""
    pruner = EphemeralToolPruner(max_lines=15, max_chars=600)
    single_huge_line = "A" * 1200

    assert pruner.should_prune(single_huge_line)
    processed, trace_id = await pruner.process_tool_output(single_huge_line)

    assert trace_id is not None
    assert "[TOOL OUTPUT TRUNCATED:" in processed
