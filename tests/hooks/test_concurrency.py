"""Concurrency tests for multi-process database access."""

from __future__ import annotations

import multiprocessing
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mem_plane.core.models import TraceRecord
from mem_plane.storage.protocols import EpisodeChunk
from mem_plane.storage.sqlite.store import SQLiteStore


def _worker_process(proc_idx: int, db_path: str, iterations: int) -> int:
    try:
        store = SQLiteStore(database=db_path)
        project_id = f"p_concurrent_{proc_idx % 2}"  # 2 projects shared across 8 workers
        session_id = f"s_proc_{proc_idx}"
        store.upsert_session(session_id, project_id, None)

        for i in range(iterations):
            # 1. Save trace (simulating post-tool-use)
            trace = TraceRecord(
                trace_id=f"t_{proc_idx:02d}_{i:04d}",
                project_id=project_id,
                session_id=session_id,
                tool_name="Bash",
                command=f"echo 'worker {proc_idx} iter {i}'",
                stdout=f"output {proc_idx} {i}",
                stderr="",
                exit_code=0,
                line_count=1,
                char_count=15,
                created_at=datetime.now(UTC),
            )
            store.save_trace(trace)

            # 2. Append chunk (simulating stop)
            chunk = EpisodeChunk(
                chunk_id=f"e_{proc_idx:02d}_{i:04d}",
                project_id=project_id,
                session_id=session_id,
                seq=i,
                segment=0,
                start_ts=None,
                end_ts=None,
                text=f"User: run command {i}\nAssistant: completed {i}",
                token_estimate=25,
                created_at=datetime.now(UTC),
            )
            store.append_chunks([chunk])

        store.close()
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"Worker {proc_idx} failed: {exc}")
        return 1


def test_concurrency_8_processes(tmp_path: Path) -> None:
    db_file = tmp_path / "concurrent.db"
    # Initialize store to set up schema and WAL mode
    init_store = SQLiteStore(database=db_file)
    init_store.close()

    num_procs = 8
    iterations = 20

    ctx = multiprocessing.get_context("spawn")
    processes: list[Any] = []

    for idx in range(num_procs):
        p = ctx.Process(target=_worker_process, args=(idx, str(db_file), iterations))
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=30)
        assert p.exitcode == 0, f"Process {p.pid} failed with exit code {p.exitcode}"

    # Verify counts in database
    verify_store = SQLiteStore(database=db_file)
    try:
        cur_t = verify_store.conn.execute("SELECT count(*) FROM tool_traces;")
        total_traces = cur_t.fetchone()[0]
        assert total_traces == num_procs * iterations

        cur_c = verify_store.conn.execute("SELECT count(*) FROM episode_chunks;")
        total_chunks = cur_c.fetchone()[0]
        assert total_chunks == num_procs * iterations

        # Assert no duplicate chunks
        cur_d = verify_store.conn.execute(
            """
            SELECT session_id, seq, count(*)
            FROM episode_chunks
            GROUP BY session_id, seq
            HAVING count(*) > 1;
            """
        )
        assert len(cur_d.fetchall()) == 0
    finally:
        verify_store.close()
