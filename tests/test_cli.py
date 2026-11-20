"""Unit tests for unified CLI commands (Section 10, M6.1, M6.2, M6.8)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mem_plane.cli import main
from mem_plane.config import Settings
from mem_plane.core.models import Fact, FactKind
from mem_plane.project import resolve_project
from mem_plane.storage.sqlite.store import SQLiteStore


@pytest.fixture
def cli_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "cli_test.db"),
        embedding_provider="dummy",
        allow_dummy_embeddings=True,
    )


def test_cli_doctor_and_print_hooks(capsys: pytest.CaptureFixture[str]) -> None:
    # 1. doctor --print-hooks
    code_hooks = main(["doctor", "--print-hooks"])
    assert code_hooks == 0
    out_hooks = capsys.readouterr().out
    data = json.loads(out_hooks)
    assert "hooks" in data
    assert "PostToolUse" in data["hooks"]
    assert "SessionStart" in data["hooks"]

    # 2. doctor
    code_doctor = main(["doctor"])
    assert code_doctor in (0, 1)
    out_doctor = capsys.readouterr().out
    assert "MEM-PLANE DIAGNOSTIC CHECK" in out_doctor


def test_cli_export_and_import(
    tmp_path: Path, cli_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("mem_plane.config.Settings", return_value=cli_settings):
        store = SQLiteStore(database=cli_settings.db_path, load_vec=False)
        proj = resolve_project(str(tmp_path))
        try:
            store.upsert_project(proj)
            store.create_fact(
                Fact(
                    fact_id="f_export01",
                    project_id=proj.project_id,
                    kind=FactKind.DECISION,
                    summary="Test export decision",
                    body="Body details",
                )
            )
        finally:
            store.close()

        # Run export
        code_exp = main(["export", "--project", str(tmp_path)])
        assert code_exp == 0
        export_out = capsys.readouterr().out
        assert "Test export decision" in export_out

        # Write export output to file
        export_file = tmp_path / "exported.jsonl"
        export_file.write_text(export_out)

        # Run import
        code_imp = main(["import", str(export_file)])
        assert code_imp == 0
        imp_out = capsys.readouterr().out
        # Fact already exists, so it should be skipped as duplicate
        assert "skipped 1 duplicates" in imp_out


def test_cli_gc(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("mem_plane.config.Settings", return_value=cli_settings):
        code_gc = main(["gc"])
        assert code_gc == 0
        out_gc = capsys.readouterr().out
        assert "Checkpoint and optimization complete" in out_gc


def test_cli_search_and_status(
    tmp_path: Path, cli_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("mem_plane.config.Settings", return_value=cli_settings):
        store = SQLiteStore(database=cli_settings.db_path, load_vec=False)
        proj = resolve_project(str(tmp_path))
        try:
            store.upsert_project(proj)
            store.create_fact(
                Fact(
                    fact_id="f_search01",
                    project_id=proj.project_id,
                    kind=FactKind.RULE,
                    summary="Unique query token alpha_beta_gamma",
                    body="Body search details",
                )
            )
        finally:
            store.close()

        # Run status
        code_status = main(["status", "--project", str(tmp_path)])
        assert code_status == 0
        out_status = capsys.readouterr().out
        assert "Active facts: 1" in out_status

        # Run search
        code_search = main(["search", "alpha_beta_gamma", "--project", str(tmp_path)])
        assert code_search == 0
        out_search = capsys.readouterr().out
        assert "f_search01" in out_search


def test_cli_bench(capsys: pytest.CaptureFixture[str]) -> None:
    fixture_path = (
        Path(__file__).parent / "fixtures" / "claude_code" / "transcript_two_compactions.jsonl"
    )
    assert fixture_path.exists()
    code_bench = main(["bench", str(fixture_path)])
    assert code_bench == 0
    out_bench = capsys.readouterr().out
    assert "MEM-PLANE TOKEN BENCHMARK REPORT" in out_bench
    assert "Cost-Weighted Input Tokens" in out_bench
    assert "Pruned Tool Outputs:" in out_bench


def test_cli_embed_pending_and_reindex(
    tmp_path: Path, cli_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("mem_plane.config.Settings", return_value=cli_settings):
        # 1. embed-pending
        code_ep = main(["embed-pending"])
        assert code_ep == 0
        out_ep = capsys.readouterr().out
        assert "Embedded" in out_ep

        # 2. reindex
        code_reindex = main(["reindex", "--model", "BAAI/bge-small-en-v1.5"])
        assert code_reindex == 0
        out_reindex = capsys.readouterr().out
        assert "Reindexed database with model 'BAAI/bge-small-en-v1.5'" in out_reindex

        # 3. reindex with OpenAI model
        code_reindex_openai = main(["reindex", "--model", "text-embedding-3-small"])
        assert code_reindex_openai == 0
        out_reindex_openai = capsys.readouterr().out
        assert "Reindexed database with model 'text-embedding-3-small'" in out_reindex_openai
