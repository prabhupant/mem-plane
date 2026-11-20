"""Synchronous SQLite implementation of MemoryStore."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from mem_plane.config import Settings

from mem_plane.core.models import (
    Fact,
    FactKind,
    FactPath,
    FactStatus,
    SearchHit,
    TraceRecord,
)
from mem_plane.project import ProjectInfo
from mem_plane.storage.protocols import (
    CreateResult,
    EpisodeChunk,
    HistoryRow,
    MemoryStore,
    PendingItem,
    SearchRequest,
    SessionRow,
)
from mem_plane.storage.sqlite.connection import create_sync_connection
from mem_plane.storage.sqlite.migrations import migrate_v0_to_v1
from mem_plane.storage.sqlite.schema import (
    compute_content_hash,
    init_schema,
    supports_vec_partition_keys,
)


class SQLiteStore(MemoryStore):
    """Synchronous SQLite implementation of MemoryStore protocol."""

    def __init__(
        self,
        database: str | Path | None = None,
        dimension: int = 384,
        model_name: str = "BAAI/bge-small-en-v1.5",
        settings: Settings | None = None,
        load_vec: bool = True,
    ) -> None:
        from mem_plane.config import Settings as ConfigSettings

        self.settings = settings or ConfigSettings()
        self.conn: Any = create_sync_connection(database, load_vec=load_vec)
        self.dimension = dimension
        self.model_name = model_name
        self.load_vec = load_vec
        if load_vec:
            self.partition_keys_supported = supports_vec_partition_keys(self.conn)
            # Attempt v0 migration if applicable
            migrate_v0_to_v1(self.conn, dimension=dimension, model_name=model_name)
            init_schema(
                self.conn,
                dimension=dimension,
                model_name=model_name,
                force_partition_keys=self.partition_keys_supported,
            )
        else:
            self.partition_keys_supported = False
            # Still ensure relational tables and FTS tables are initialized
            init_schema(
                self.conn,
                dimension=dimension,
                model_name=model_name,
                force_partition_keys=False,
                include_vec=False,
            )

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    # ---------- projects / sessions ----------

    def upsert_project(self, info: ProjectInfo) -> None:
        now = datetime.now(UTC).isoformat()
        sql = """
        INSERT INTO projects (project_id, display_name, root_path, remote_url, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            root_path = excluded.root_path,
            last_seen_at = excluded.last_seen_at;
        """
        self.conn.execute(
            sql, (info.project_id, info.display_name, info.root_path, info.remote_url, now, now)
        )
        self.conn.commit()

    def get_project(self, project_id: str) -> ProjectInfo | None:
        cur = self.conn.execute(
            "SELECT project_id, display_name, root_path, remote_url FROM projects WHERE project_id = ?;",
            (project_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return ProjectInfo(
            project_id=row[0], display_name=row[1], root_path=row[2], remote_url=row[3]
        )

    def upsert_session(
        self, session_id: str, project_id: str, transcript_path: str | None
    ) -> SessionRow:
        now = datetime.now(UTC).isoformat()
        sql = """
        INSERT INTO sessions (
            session_id, project_id, harness, transcript_path, transcript_offset,
            compactions, started_at, last_event_at, ended_at
        ) VALUES (?, ?, 'claude-code', ?, 0, 0, ?, ?, NULL)
        ON CONFLICT(session_id) DO UPDATE SET
            transcript_path = COALESCE(excluded.transcript_path, sessions.transcript_path),
            last_event_at = excluded.last_event_at;
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                VALUES (?, ?, '', ?, ?);
                """,
                (project_id, project_id, now, now),
            )
            self.conn.execute(sql, (session_id, project_id, transcript_path, now, now))

        cur = self.conn.execute(
            """
            SELECT session_id, project_id, harness, transcript_path, transcript_offset,
                   compactions, started_at, last_event_at, ended_at
            FROM sessions WHERE session_id = ?;
            """,
            (session_id,),
        )
        r = cur.fetchone()
        return SessionRow(
            session_id=r[0],
            project_id=r[1],
            harness=r[2],
            transcript_path=r[3],
            transcript_offset=r[4],
            compactions=r[5],
            started_at=datetime.fromisoformat(r[6]),
            last_event_at=datetime.fromisoformat(r[7]),
            ended_at=datetime.fromisoformat(r[8]) if r[8] else None,
        )

    def advance_transcript_offset(self, session_id: str, new_offset: int) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "UPDATE sessions SET transcript_offset = ?, last_event_at = ? WHERE session_id = ?;",
            (new_offset, now, session_id),
        )
        self.conn.commit()

    def increment_compactions(self, session_id: str) -> int:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "UPDATE sessions SET compactions = compactions + 1, last_event_at = ? WHERE session_id = ?;",
            (now, session_id),
        )
        self.conn.commit()
        cur = self.conn.execute(
            "SELECT compactions FROM sessions WHERE session_id = ?;", (session_id,)
        )
        row = cur.fetchone()
        return int(row[0]) if row else 1

    # ---------- facts ----------

    def create_fact(self, fact: Fact) -> CreateResult:
        content_hash = compute_content_hash(fact.summary, fact.body)
        now_dt = datetime.now(UTC)
        now_iso = now_dt.isoformat()

        # Step 1: Dedup check by content_hash among active facts in this project
        cur = self.conn.execute(
            """
            SELECT fact_id, project_id, session_id, kind, summary, body, status,
                   supersedes, superseded_by, pinned, source, created_at, updated_at
            FROM facts
            WHERE project_id = ? AND content_hash = ? AND status = 'active';
            """,
            (fact.project_id, content_hash),
        )
        existing_row = cur.fetchone()
        if existing_row:
            existing_fact = self._row_to_fact(existing_row)
            return CreateResult(
                status="duplicate", fact=existing_fact, duplicate_of_id=existing_fact.fact_id
            )

        superseded_fact: Fact | None = None

        with self.conn:
            # Ensure project exists so foreign key is satisfied
            self.conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                VALUES (?, ?, '', ?, ?);
                """,
                (fact.project_id, fact.project_id, now_iso, now_iso),
            )

            # Step 2: Check if supersedes target exists
            if fact.supersedes:
                cur_super = self.conn.execute(
                    """
                    SELECT fact_id, project_id, session_id, kind, summary, body, status,
                           supersedes, superseded_by, pinned, source, created_at, updated_at
                    FROM facts
                    WHERE fact_id = ? AND project_id = ? AND status = 'active';
                    """,
                    (fact.supersedes, fact.project_id),
                )
                super_row = cur_super.fetchone()
                if super_row:
                    superseded_fact = self._row_to_fact(super_row)

            # Step 3: Insert new fact row
            self.conn.execute(
                """
                INSERT INTO facts (
                    fact_id, project_id, session_id, kind, summary, body, status,
                    supersedes, superseded_by, pinned, source, content_hash,
                    created_at, updated_at, embedded_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?, ?, NULL);
                """,
                (
                    fact.fact_id,
                    fact.project_id,
                    fact.session_id,
                    fact.kind.value,
                    fact.summary,
                    fact.body,
                    fact.supersedes,
                    1 if fact.pinned else 0,
                    fact.source,
                    content_hash,
                    now_iso,
                    now_iso,
                ),
            )

            # Step 4: Update old fact if superseded
            if superseded_fact:
                self.conn.execute(
                    """
                    UPDATE facts
                    SET status = 'superseded', superseded_by = ?, updated_at = ?
                    WHERE fact_id = ? AND project_id = ?;
                    """,
                    (fact.fact_id, now_iso, fact.supersedes, fact.project_id),
                )
                if self.partition_keys_supported:
                    try:
                        self.conn.execute(
                            "UPDATE facts_vec SET status = 'superseded' WHERE fact_id = ?;",
                            (fact.supersedes,),
                        )
                    except sqlite3.Error:
                        pass
                self.conn.execute(
                    """
                    INSERT INTO fact_history (fact_id, event, actor, session_id, created_at)
                    VALUES (?, 'supersede', ?, ?, ?);
                    """,
                    (fact.supersedes, fact.source, fact.session_id, now_iso),
                )

            # Step 5: Insert FTS row
            self.conn.execute(
                "INSERT INTO facts_fts (fact_id, project_id, summary, body) VALUES (?, ?, ?, ?);",
                (fact.fact_id, fact.project_id, fact.summary, fact.body),
            )

            # Step 5: Insert fact_paths
            for p in fact.paths:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO fact_paths (fact_id, rel_path, content_sha256, git_commit)
                    VALUES (?, ?, ?, ?);
                    """,
                    (fact.fact_id, p.rel_path, p.content_sha256, p.git_commit),
                )

            # Step 6: Insert audit history row
            self.conn.execute(
                """
                INSERT INTO fact_history (
                    fact_id, event, new_summary, new_body, actor, session_id, created_at
                ) VALUES (?, 'create', ?, ?, ?, ?, ?);
                """,
                (fact.fact_id, fact.summary, fact.body, fact.source, fact.session_id, now_iso),
            )

        saved_fact = fact.model_copy(
            update={"created_at": now_dt, "updated_at": now_dt, "status": FactStatus.ACTIVE}
        )
        result_status: Literal["created", "superseded"] = (
            "superseded" if superseded_fact else "created"
        )
        return CreateResult(status=result_status, fact=saved_fact, superseded_fact=superseded_fact)

    def update_fact(
        self,
        fact_id: str,
        project_id: str,
        *,
        summary: str | None = None,
        body: str | None = None,
        kind: FactKind | None = None,
        actor: str = "agent",
        session_id: str | None = None,
    ) -> Fact:
        existing = self.get_fact(fact_id, project_id)
        if not existing:
            raise KeyError(f"Fact {fact_id} not found in project {project_id}")

        new_summary = summary if summary is not None else existing.summary
        new_body = body if body is not None else existing.body
        new_kind = kind if kind is not None else existing.kind
        new_content_hash = compute_content_hash(new_summary, new_body)
        now_dt = datetime.now(UTC)
        now_iso = now_dt.isoformat()

        with self.conn:
            self.conn.execute(
                """
                UPDATE facts
                SET summary = ?, body = ?, kind = ?, content_hash = ?,
                    updated_at = ?, embedded_at = NULL
                WHERE fact_id = ? AND project_id = ?;
                """,
                (
                    new_summary,
                    new_body,
                    new_kind.value,
                    new_content_hash,
                    now_iso,
                    fact_id,
                    project_id,
                ),
            )
            # Refresh FTS
            self.conn.execute("DELETE FROM facts_fts WHERE fact_id = ?;", (fact_id,))
            self.conn.execute(
                "INSERT INTO facts_fts (fact_id, project_id, summary, body) VALUES (?, ?, ?, ?);",
                (fact_id, project_id, new_summary, new_body),
            )
            # Record history
            self.conn.execute(
                """
                INSERT INTO fact_history (
                    fact_id, event, old_summary, new_summary, old_body, new_body,
                    actor, session_id, created_at
                ) VALUES (?, 'update', ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    fact_id,
                    existing.summary,
                    new_summary,
                    existing.body,
                    new_body,
                    actor,
                    session_id,
                    now_iso,
                ),
            )

        updated = self.get_fact(fact_id, project_id)
        assert updated is not None
        return updated

    def delete_fact(
        self, fact_id: str, project_id: str, actor: str = "agent", session_id: str | None = None
    ) -> None:
        existing = self.get_fact(fact_id, project_id)
        if not existing:
            return

        now_iso = datetime.now(UTC).isoformat()
        with self.conn:
            self.conn.execute(
                "UPDATE facts SET status = 'deleted', updated_at = ? WHERE fact_id = ? AND project_id = ?;",
                (now_iso, fact_id, project_id),
            )
            self.conn.execute("DELETE FROM facts_fts WHERE fact_id = ?;", (fact_id,))
            try:
                self.conn.execute("DELETE FROM facts_vec WHERE fact_id = ?;", (fact_id,))
            except sqlite3.Error:
                pass
            self.conn.execute(
                """
                INSERT INTO fact_history (
                    fact_id, event, old_summary, old_body, actor, session_id, created_at
                ) VALUES (?, 'delete', ?, ?, ?, ?, ?);
                """,
                (fact_id, existing.summary, existing.body, actor, session_id, now_iso),
            )

    def set_pinned(self, fact_id: str, project_id: str, pinned: bool, actor: str = "agent") -> None:
        now_iso = datetime.now(UTC).isoformat()
        event = "pin" if pinned else "unpin"
        with self.conn:
            self.conn.execute(
                "UPDATE facts SET pinned = ?, updated_at = ? WHERE fact_id = ? AND project_id = ?;",
                (1 if pinned else 0, now_iso, fact_id, project_id),
            )
            self.conn.execute(
                "INSERT INTO fact_history (fact_id, event, actor, created_at) VALUES (?, ?, ?, ?);",
                (fact_id, event, actor, now_iso),
            )

    def get_fact(self, fact_id: str, project_id: str) -> Fact | None:
        cur = self.conn.execute(
            """
            SELECT fact_id, project_id, session_id, kind, summary, body, status,
                   supersedes, superseded_by, pinned, source, created_at, updated_at
            FROM facts
            WHERE fact_id = ? AND project_id = ? AND status != 'deleted';
            """,
            (fact_id, project_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_fact(row)

    def list_pinned(self, project_id: str) -> list[Fact]:
        cur = self.conn.execute(
            """
            SELECT fact_id, project_id, session_id, kind, summary, body, status,
                   supersedes, superseded_by, pinned, source, created_at, updated_at
            FROM facts
            WHERE project_id = ? AND pinned = 1 AND status = 'active'
            ORDER BY kind ASC, created_at ASC, fact_id ASC;
            """,
            (project_id,),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def list_recent_facts(self, project_id: str, limit: int = 8) -> list[Fact]:
        cur = self.conn.execute(
            """
            SELECT fact_id, project_id, session_id, kind, summary, body, status,
                   supersedes, superseded_by, pinned, source, created_at, updated_at
            FROM facts
            WHERE project_id = ? AND status = 'active' AND pinned = 0
            ORDER BY updated_at DESC, fact_id ASC
            LIMIT ?;
            """,
            (project_id, limit),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def list_session_facts(self, project_id: str, session_id: str) -> list[Fact]:
        cur = self.conn.execute(
            """
            SELECT fact_id, project_id, session_id, kind, summary, body, status,
                   supersedes, superseded_by, pinned, source, created_at, updated_at
            FROM facts
            WHERE project_id = ? AND session_id = ? AND status = 'active'
            ORDER BY created_at ASC, fact_id ASC;
            """,
            (project_id, session_id),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def fact_history(self, fact_id: str, project_id: str) -> list[HistoryRow]:
        # Validate fact belongs to project
        check_cur = self.conn.execute(
            "SELECT 1 FROM facts WHERE fact_id = ? AND project_id = ?;", (fact_id, project_id)
        )
        if not check_cur.fetchone():
            return []

        cur = self.conn.execute(
            """
            SELECT history_id, fact_id, event, old_summary, new_summary,
                   old_body, new_body, actor, session_id, created_at
            FROM fact_history
            WHERE fact_id = ?
            ORDER BY created_at ASC, history_id ASC;
            """,
            (fact_id,),
        )
        rows: list[HistoryRow] = []
        for r in cur.fetchall():
            rows.append(
                HistoryRow(
                    history_id=r[0],
                    fact_id=r[1],
                    event=r[2],
                    old_summary=r[3],
                    new_summary=r[4],
                    old_body=r[5],
                    new_body=r[6],
                    actor=r[7],
                    session_id=r[8],
                    created_at=datetime.fromisoformat(r[9]),
                )
            )
        return rows

    # ---------- episodes ----------

    def append_chunks(self, chunks: list[EpisodeChunk]) -> None:
        if not chunks:
            return
        with self.conn:
            for ch in chunks:
                now_iso = ch.created_at.isoformat()
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                    VALUES (?, ?, '', ?, ?);
                    """,
                    (ch.project_id, ch.project_id, now_iso, now_iso),
                )
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO sessions (session_id, project_id, started_at, last_event_at)
                    VALUES (?, ?, ?, ?);
                    """,
                    (ch.session_id, ch.project_id, now_iso, now_iso),
                )
                self.conn.execute(
                    """
                    INSERT INTO episode_chunks (
                        chunk_id, project_id, session_id, seq, segment,
                        start_ts, end_ts, text, token_estimate, created_at, embedded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL);
                    """,
                    (
                        ch.chunk_id,
                        ch.project_id,
                        ch.session_id,
                        ch.seq,
                        ch.segment,
                        ch.start_ts,
                        ch.end_ts,
                        ch.text,
                        ch.token_estimate,
                        now_iso,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO episode_fts (chunk_id, project_id, text) VALUES (?, ?, ?);",
                    (ch.chunk_id, ch.project_id, ch.text),
                )

    def get_chunk(self, chunk_id: str, project_id: str) -> EpisodeChunk | None:
        cur = self.conn.execute(
            """
            SELECT chunk_id, project_id, session_id, seq, segment,
                   start_ts, end_ts, text, token_estimate, created_at, embedded_at
            FROM episode_chunks
            WHERE chunk_id = ? AND project_id = ?;
            """,
            (chunk_id, project_id),
        )
        r = cur.fetchone()
        if not r:
            return None
        return EpisodeChunk(
            chunk_id=r[0],
            project_id=r[1],
            session_id=r[2],
            seq=r[3],
            segment=r[4],
            start_ts=r[5],
            end_ts=r[6],
            text=r[7],
            token_estimate=r[8],
            created_at=datetime.fromisoformat(r[9]),
            embedded_at=datetime.fromisoformat(r[10]) if r[10] else None,
        )

    # ---------- traces ----------

    def save_trace(self, trace: TraceRecord, retention_days: int = 14) -> None:
        created_iso = trace.created_at.isoformat()
        expires_iso = (trace.created_at + timedelta(days=retention_days)).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO projects (project_id, display_name, root_path, created_at, last_seen_at)
                VALUES (?, ?, '', ?, ?);
                """,
                (trace.project_id, trace.project_id, created_iso, created_iso),
            )
            self.conn.execute(
                """
                INSERT INTO tool_traces (
                    trace_id, project_id, session_id, tool_name, tool_use_id,
                    command, stdout, stderr, exit_code, line_count, char_count,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    trace.trace_id,
                    trace.project_id,
                    trace.session_id,
                    trace.tool_name,
                    None,
                    trace.command,
                    trace.stdout,
                    trace.stderr,
                    trace.exit_code,
                    trace.line_count,
                    trace.char_count,
                    created_iso,
                    expires_iso,
                ),
            )

    def get_trace(self, trace_id: str, project_id: str) -> TraceRecord | None:
        cur = self.conn.execute(
            """
            SELECT trace_id, project_id, session_id, tool_name, command,
                   stdout, stderr, exit_code, line_count, char_count, created_at
            FROM tool_traces
            WHERE trace_id = ? AND project_id = ?;
            """,
            (trace_id, project_id),
        )
        r = cur.fetchone()
        if not r:
            return None
        return TraceRecord(
            trace_id=r[0],
            project_id=r[1],
            session_id=r[2],
            tool_name=r[3],
            command=r[4],
            stdout=r[5],
            stderr=r[6],
            exit_code=r[7],
            line_count=r[8],
            char_count=r[9],
            created_at=datetime.fromisoformat(r[10]),
        )

    def purge_expired_traces(self, now: datetime | None = None) -> int:
        now_iso = (now or datetime.now(UTC)).isoformat()
        with self.conn:
            cur = self.conn.execute("DELETE FROM tool_traces WHERE expires_at <= ?;", (now_iso,))
            return int(cur.rowcount)

    # ---------- vectors ----------

    def pending_embeddings(self, limit: int = 50) -> list[PendingItem]:
        items: list[PendingItem] = []

        # Pending facts
        cur_f = self.conn.execute(
            """
            SELECT fact_id, project_id, summary, body
            FROM facts
            WHERE embedded_at IS NULL AND status != 'deleted'
            ORDER BY created_at ASC
            LIMIT ?;
            """,
            (limit,),
        )
        for r in cur_f.fetchall():
            text = f"{r[2]}\n{r[3]}"
            items.append(
                PendingItem(ref=r[0], ref_type="fact", project_id=r[1], text_to_embed=text)
            )

        remaining = limit - len(items)
        if remaining > 0:
            cur_e = self.conn.execute(
                """
                SELECT chunk_id, project_id, text
                FROM episode_chunks
                WHERE embedded_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?;
                """,
                (remaining,),
            )
            for r in cur_e.fetchall():
                items.append(
                    PendingItem(ref=r[0], ref_type="episode", project_id=r[1], text_to_embed=r[2])
                )

        return items

    def write_vectors(self, items: list[tuple[PendingItem, list[float]]]) -> None:
        if not items:
            return
        now_iso = datetime.now(UTC).isoformat()
        with self.conn:
            for item, vector in items:
                if item.ref_type == "fact":
                    # Look up fact status
                    cur_st = self.conn.execute(
                        "SELECT status FROM facts WHERE fact_id = ?;", (item.ref,)
                    )
                    row_st = cur_st.fetchone()
                    status = row_st[0] if row_st else "active"

                    if self.partition_keys_supported:
                        self.conn.execute(
                            "INSERT OR REPLACE INTO facts_vec (fact_id, project_id, status, embedding) VALUES (?, ?, ?, ?);",
                            (item.ref, item.project_id, status, str(vector)),
                        )
                    else:
                        self.conn.execute(
                            "INSERT OR REPLACE INTO facts_vec (fact_id, embedding) VALUES (?, ?);",
                            (item.ref, str(vector)),
                        )
                    self.conn.execute(
                        "UPDATE facts SET embedded_at = ? WHERE fact_id = ?;", (now_iso, item.ref)
                    )
                elif item.ref_type == "episode":
                    if self.partition_keys_supported:
                        self.conn.execute(
                            "INSERT OR REPLACE INTO episode_vec (chunk_id, project_id, embedding) VALUES (?, ?, ?);",
                            (item.ref, item.project_id, str(vector)),
                        )
                    else:
                        self.conn.execute(
                            "INSERT OR REPLACE INTO episode_vec (chunk_id, embedding) VALUES (?, ?);",
                            (item.ref, str(vector)),
                        )
                    self.conn.execute(
                        "UPDATE episode_chunks SET embedded_at = ? WHERE chunk_id = ?;",
                        (now_iso, item.ref),
                    )

    # ---------- search ----------

    def search(
        self, req: SearchRequest, query_vector: list[float] | None = None
    ) -> list[SearchHit]:
        proj = self.get_project(req.project_id)
        project_root = proj.root_path if proj else None

        from mem_plane.storage.sqlite.search import execute_hybrid_search

        return execute_hybrid_search(
            conn=self.conn,
            req=req,
            query_vector=query_vector,
            partition_keys=self.partition_keys_supported,
            settings=self.settings,
            project_root=project_root,
        )

    def _row_to_fact(self, row: tuple[Any, ...]) -> Fact:
        fact_id = row[0]
        cur_paths = self.conn.execute(
            "SELECT rel_path, content_sha256, git_commit FROM fact_paths WHERE fact_id = ?;",
            (fact_id,),
        )
        paths = [
            FactPath(rel_path=pr[0], content_sha256=pr[1], git_commit=pr[2])
            for pr in cur_paths.fetchall()
        ]

        return Fact(
            fact_id=row[0],
            project_id=row[1],
            session_id=row[2],
            kind=FactKind(row[3]),
            summary=row[4],
            body=row[5],
            status=FactStatus(row[6]),
            supersedes=row[7],
            superseded_by=row[8],
            pinned=bool(row[9]),
            source=row[10],
            paths=paths,
            created_at=datetime.fromisoformat(row[11]),
            updated_at=datetime.fromisoformat(row[12]),
        )
