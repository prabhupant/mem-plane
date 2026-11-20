"""Tests for benchmark and token usage reporting."""

from __future__ import annotations

from pathlib import Path

from mem_plane.bench import analyze_transcript, run_bench


def test_analyze_transcript_simple() -> None:
    path = Path(__file__).parent / "fixtures" / "claude_code" / "transcript_simple.jsonl"
    stats = analyze_transcript(str(path))

    assert stats.requests > 0
    assert stats.cache_write_tokens > 0
    assert stats.cache_read_tokens > 0
    assert stats.compactions == 0
    assert stats.weighted_input_tokens > 0


def test_analyze_transcript_compacted() -> None:
    path = Path(__file__).parent / "fixtures" / "claude_code" / "transcript_compacted.jsonl"
    stats = analyze_transcript(str(path))

    assert stats.requests > 0
    assert stats.compactions == 1
    assert stats.weighted_input_tokens > 0


def test_run_bench_cli(capsys: object) -> None:
    path = Path(__file__).parent / "fixtures" / "claude_code" / "transcript_simple.jsonl"
    exit_code = run_bench([str(path)])
    assert exit_code == 0
