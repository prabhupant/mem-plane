"""Core service layer implementing the 7 MCP tools (Section 8.2)."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mem_plane.core.models import Fact, FactKind, FactPath, FactStatus
from mem_plane.core.staleness import check_fact_staleness
from mem_plane.server.formatting import format_search_results
from mem_plane.storage.protocols import PendingItem, SearchRequest
from mem_plane.storage.sqlite.schema import compute_content_hash
from mem_plane.storage.sqlite.search import search_facts_vector
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.pipeline.embedder_worker import BackgroundEmbedderWorker
    from mem_plane.pipeline.embeddings import EmbeddingClient
    from mem_plane.storage.protocols import MemoryStore

logger = logging.getLogger(__name__)


def _get_git_commit(root: Path) -> str | None:
    """Safely obtain current git HEAD commit SHA without throwing."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


class MemPlaneService:
    """Service layer exposing memory operations to MCP server and CLI."""

    def __init__(
        self,
        store: MemoryStore,
        project_id: str,
        embedding_client: EmbeddingClient | None = None,
        settings: Settings | None = None,
        project_root: str | None = None,
        embedder_worker: BackgroundEmbedderWorker | None = None,
    ) -> None:
        self.store = store
        self.project_id = project_id
        self.embedding_client = embedding_client
        self.settings = settings
        self.project_root = project_root
        self.embedder_worker = embedder_worker

    # ------------------------------------------------------------------
    # 1. search_memory
    # ------------------------------------------------------------------
    async def search_memory(
        self,
        query: str,
        top_k: int = 5,
        include_episodes: bool = True,
    ) -> str:
        """Search memory using hybrid keyword + vector retrieval and weighted RRF."""
        try:
            clamped_k = min(max(top_k, 1), 20)
            query_vector: list[float] | None = None
            if self.embedding_client is not None:
                try:
                    query_vector = await self.embedding_client.embed_query(query)
                except (RuntimeError, ValueError, OSError) as e:
                    logger.warning("Failed to compute query vector: %s", e)

            req = SearchRequest(
                project_id=self.project_id,
                query=query,
                top_k=clamped_k,
                include_episodes=include_episodes,
            )
            hits = self.store.search(req, query_vector=query_vector)
            return format_search_results(hits, query=query, project_id=self.project_id)
        except Exception as e:
            logger.exception("Error in search_memory")
            return f"error: {e}"

    # ------------------------------------------------------------------
    # 2. get_memory
    # ------------------------------------------------------------------
    async def get_memory(
        self,
        ref: str,
        grep: str | None = None,
        lines: str | None = None,
        max_chars: int = 6000,
    ) -> str:
        """Retrieve full details for fact (f_), episode (e_), or trace (t_)."""
        try:
            ref = ref.strip()
            if ref.startswith("f_"):
                return self._get_fact_detail(ref)
            if ref.startswith("e_"):
                return self._get_episode_detail(ref)
            if ref.startswith("t_"):
                return self._get_trace_detail(ref, grep=grep, lines=lines, max_chars=max_chars)
            return f"error: invalid memory reference '{ref}'"
        except Exception as e:
            logger.exception("Error in get_memory")
            return f"error: {e}"

    def _get_fact_detail(self, fact_id: str) -> str:
        fact = self.store.get_fact(fact_id, self.project_id)
        if fact is None or fact.status == FactStatus.DELETED:
            return f"error: fact '{fact_id}' not found"

        stale_reason = check_fact_staleness(fact, self.project_root)
        history = self.store.fact_history(fact_id, self.project_id)

        header_parts = [fact.fact_id, f"[{fact.kind.value}]"]
        if fact.pinned:
            header_parts.append("[PINNED]")
        if stale_reason:
            header_parts.append(f"[STALE: {stale_reason}]")
        header = " ".join(header_parts)

        lines = [
            header,
            f"Status: {fact.status.value}",
            f"Created: {fact.created_at.strftime('%Y-%m-%d')}",
            f"Summary: {fact.summary}",
            f"Body:\n{fact.body}",
        ]

        if fact.paths:
            lines.append("Paths:")
            for p in fact.paths:
                sha_short = p.content_sha256[:12] if p.content_sha256 else "none"
                commit_short = p.git_commit[:8] if p.git_commit else "none"
                lines.append(f"  - {p.rel_path} (sha256: {sha_short}, git: {commit_short})")

        lines.append(f"History: {len(history)} revisions")
        return "\n".join(lines)

    def _get_episode_detail(self, chunk_id: str) -> str:
        chunk = self.store.get_chunk(chunk_id, self.project_id)
        if chunk is None:
            return f"error: episode chunk '{chunk_id}' not found"

        lines = [
            f"{chunk.chunk_id} [episode] (session: {chunk.session_id}, seq: {chunk.seq})",
            f"Tokens: ~{chunk.token_estimate}",
            f"Created: {chunk.created_at.strftime('%Y-%m-%d')}",
            "---",
            chunk.text,
        ]
        return "\n".join(lines)

    def _get_trace_detail(
        self,
        trace_id: str,
        grep: str | None = None,
        lines: str | None = None,
        max_chars: int = 6000,
    ) -> str:
        trace = self.store.get_trace(trace_id, self.project_id)
        if trace is None:
            return f"error: trace '{trace_id}' not found"

        out_lines = [
            f"{trace.trace_id} [trace] (tool: {trace.tool_name}, exit: {trace.exit_code})",
        ]
        if trace.command:
            out_lines.append(f"Command: {trace.command}")

        def _format_stream(name: str, text: str) -> list[str]:
            if not text:
                return []
            raw_lines = text.splitlines()
            header = f"=== {name} ({len(raw_lines)} lines, {len(text)} chars) ==="
            stream_res: list[str] = [header]

            if grep:
                try:
                    pattern = re.compile(grep, re.IGNORECASE)
                except re.error as err:
                    return [f"error: invalid grep regex: {err}"]

                matches = [i for i, l in enumerate(raw_lines) if pattern.search(l)]
                if not matches:
                    stream_res.append("No matching lines found.")
                    return stream_res

                keep: set[int] = set()
                for idx in matches[:200]:
                    if idx > 0:
                        keep.add(idx - 1)
                    keep.add(idx)
                    if idx + 1 < len(raw_lines):
                        keep.add(idx + 1)

                for idx in sorted(keep):
                    marker = ">" if idx in matches else " "
                    stream_res.append(f"{marker} {idx + 1:5d} | {raw_lines[idx]}")

                if len(matches) > 200:
                    stream_res.append(f"... ({len(matches) - 200} additional matches omitted) ...")
                return stream_res

            if lines:
                raw_lines = text.splitlines()
                if "-" in lines:
                    p1, p2 = lines.split("-", 1)
                    start = max(1, int(p1.strip()))
                    end = min(len(raw_lines), int(p2.strip()))
                else:
                    start = max(1, int(lines.strip()))
                    end = start

                for idx in range(start - 1, end):
                    stream_res.append(f"  {idx + 1:5d} | {raw_lines[idx]}")
                return stream_res

            # Default truncation
            if len(text) > max_chars:
                stream_res.append(text[:max_chars])
                stream_res.append(
                    f"\n[Output truncated to {max_chars} chars. Total: {len(raw_lines)} lines ({len(text)} chars). "
                    f'Use grep="pattern" or lines="start-end" to inspect specific sections.]'
                )
            else:
                stream_res.append(text)
            return stream_res

        out_lines.extend(_format_stream("STDOUT", trace.stdout))
        out_lines.extend(_format_stream("STDERR", trace.stderr))
        return "\n".join(out_lines)

    # ------------------------------------------------------------------
    # 3. record_fact
    # ------------------------------------------------------------------
    async def record_fact(
        self,
        summary: str,
        body: str,
        kind: FactKind | str,
        paths: list[str] | None = None,
        supersedes: str | None = None,
        pin: bool = False,
    ) -> str:
        """Record a fact, handle dedup, check similarity, and write vector."""
        try:
            summary = summary.strip()
            body = body.strip()
            if not summary or not body:
                return "error: summary and body cannot be empty"

            if isinstance(kind, str):
                try:
                    fact_kind = FactKind(kind.lower())
                except ValueError:
                    return f"error: invalid fact kind '{kind}'"
            else:
                fact_kind = kind

            content_hash = compute_content_hash(summary, body)

            # Check for existing active duplicate in project
            from mem_plane.storage.sqlite.store import SQLiteStore

            if isinstance(self.store, SQLiteStore):
                cur = self.store.conn.execute(
                    "SELECT fact_id FROM facts WHERE project_id = ? AND content_hash = ? AND status = 'active';",
                    (self.project_id, content_hash),
                )
                existing = cur.fetchone()
                if existing:
                    return f"duplicate of {existing[0]}"

            # Check similar-fact hint at cosine similarity >= 0.92 (distance <= 0.08)
            hint = ""
            new_vector: list[float] | None = None
            if self.embedding_client is not None:
                try:
                    text_to_embed = f"{summary}\n{body}"
                    new_vector = await self.embedding_client.embed_query(text_to_embed)
                    if isinstance(self.store, SQLiteStore) and not supersedes:
                        similar_ids = search_facts_vector(
                            self.store.conn,
                            project_id=self.project_id,
                            query_vector=new_vector,
                            n=5,
                            partition_keys=self.store.partition_keys_supported,
                            max_cosine_distance=0.08,
                        )
                        if similar_ids:
                            sim_id = similar_ids[0]
                            sim_fact = self.store.get_fact(sim_id, self.project_id)
                            if sim_fact:
                                hint = f'\nsimilar to {sim_id} "{sim_fact.summary}". If this replaces it, call update_memory or set supersedes.'
                except (RuntimeError, ValueError, OSError) as e:
                    logger.warning("Failed to check similar facts: %s", e)

            # Resolve paths
            fact_paths: list[FactPath] = []
            root = Path(self.project_root) if self.project_root else None
            git_commit = _get_git_commit(root) if root else None

            for p_str in paths or []:
                p_str = p_str.strip()
                if not p_str:
                    continue
                if root is not None:
                    try:
                        resolved = (root / p_str).resolve()
                        if not str(resolved).startswith(str(root.resolve())):
                            return f"error: path '{p_str}' is outside project root"
                        file_sha: str | None = None
                        if resolved.is_file():
                            file_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
                        fact_paths.append(
                            FactPath(
                                rel_path=p_str,
                                content_sha256=file_sha,
                                git_commit=git_commit,
                            )
                        )
                    except (ValueError, OSError) as err:
                        return f"error: invalid path '{p_str}': {err}"
                else:
                    fact_paths.append(
                        FactPath(rel_path=p_str, content_sha256=None, git_commit=None)
                    )

            fact_id = f"f_{secrets.token_hex(6)}"
            fact = Fact(
                fact_id=fact_id,
                project_id=self.project_id,
                kind=fact_kind,
                summary=summary,
                body=body,
                status=FactStatus.ACTIVE,
                supersedes=supersedes,
                pinned=pin,
                source="agent",
                paths=fact_paths,
            )

            res = self.store.create_fact(fact)
            if res.status == "duplicate":
                return f"duplicate of {res.fact.fact_id}"

            # Write vector immediately in second transaction
            if new_vector is not None:
                item = PendingItem(
                    ref=fact.fact_id,
                    ref_type="fact",
                    project_id=self.project_id,
                    text_to_embed=f"{summary}\n{body}",
                )
                self.store.write_vectors([(item, new_vector)])

            if self.embedder_worker is not None:
                self.embedder_worker.trigger()

            if supersedes:
                return f"recorded {fact.fact_id} superseding {supersedes}{hint}"
            return f"recorded {fact.fact_id}{hint}"

        except Exception as e:
            logger.exception("Error in record_fact")
            return f"error: {e}"

    # ------------------------------------------------------------------
    # 4. update_memory
    # ------------------------------------------------------------------
    async def update_memory(
        self,
        fact_id: str,
        summary: str | None = None,
        body: str | None = None,
        kind: FactKind | str | None = None,
    ) -> str:
        """Update an existing fact's summary, body, or kind and refresh path hashes."""
        try:
            fact_id = fact_id.strip()
            fact = self.store.get_fact(fact_id, self.project_id)
            if fact is None or fact.status == FactStatus.DELETED:
                return f"error: fact '{fact_id}' not found"

            fact_kind: FactKind | None = None
            if kind is not None:
                if isinstance(kind, str):
                    try:
                        fact_kind = FactKind(kind.lower())
                    except ValueError:
                        return f"error: invalid fact kind '{kind}'"
                else:
                    fact_kind = kind

            self.store.update_fact(
                fact_id=fact_id,
                project_id=self.project_id,
                summary=summary,
                body=body,
                kind=fact_kind,
                actor="agent",
            )

            # Refresh path hashes
            if self.project_root and isinstance(self.store, SQLiteStore):
                root = Path(self.project_root)
                for p in fact.paths:
                    file_path = root / p.rel_path
                    if file_path.is_file():
                        new_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
                        self.store.conn.execute(
                            "UPDATE fact_paths SET content_sha256 = ? WHERE fact_id = ? AND rel_path = ?;",
                            (new_sha, fact_id, p.rel_path),
                        )

            if self.embedder_worker is not None:
                self.embedder_worker.trigger()

            return f"updated {fact_id}"
        except Exception as e:
            logger.exception("Error in update_memory")
            return f"error: {e}"

    # ------------------------------------------------------------------
    # 5. forget_memory
    # ------------------------------------------------------------------
    async def forget_memory(self, fact_id: str, reason: str) -> str:
        """Soft-delete a fact with reason."""
        try:
            fact_id = fact_id.strip()
            fact = self.store.get_fact(fact_id, self.project_id)
            if fact is None or fact.status == FactStatus.DELETED:
                return f"error: fact '{fact_id}' not found"

            self.store.delete_fact(fact_id, self.project_id, actor="agent")

            if isinstance(self.store, SQLiteStore):
                now_iso = datetime.now(UTC).isoformat()
                self.store.conn.execute(
                    """
                    INSERT INTO fact_history (fact_id, event, old_summary, new_summary, old_body, new_body, actor, created_at)
                    VALUES (?, 'delete', ?, NULL, ?, ?, 'agent', ?);
                    """,
                    (fact_id, fact.summary, fact.body, reason, now_iso),
                )

            return f"forgot {fact_id}: {reason}"
        except Exception as e:
            logger.exception("Error in forget_memory")
            return f"error: {e}"

    # ------------------------------------------------------------------
    # 6. pin_memory
    # ------------------------------------------------------------------
    async def pin_memory(self, fact_id: str, pinned: bool) -> str:
        """Pin or unpin a fact."""
        try:
            fact_id = fact_id.strip()
            fact = self.store.get_fact(fact_id, self.project_id)
            if fact is None or fact.status == FactStatus.DELETED:
                return f"error: fact '{fact_id}' not found"

            self.store.set_pinned(fact_id, self.project_id, pinned=pinned, actor="agent")
            action = "pinned" if pinned else "unpinned"
            return f"{action} {fact_id}"
        except Exception as e:
            logger.exception("Error in pin_memory")
            return f"error: {e}"

    # ------------------------------------------------------------------
    # 7. memory_status
    # ------------------------------------------------------------------
    async def memory_status(self) -> str:
        """Return memory status metrics in plain text."""
        try:
            if not isinstance(self.store, SQLiteStore):
                return f"Project: {self.project_id}\nStorage: non-sqlite backend"

            conn = self.store.conn
            cur = conn.execute(
                "SELECT count(*) FROM facts WHERE project_id = ? AND status = 'active';",
                (self.project_id,),
            )
            active_facts = cur.fetchone()[0]

            cur = conn.execute(
                "SELECT count(*) FROM facts WHERE project_id = ? AND status = 'active' AND pinned = 1;",
                (self.project_id,),
            )
            pinned_facts = cur.fetchone()[0]

            cur = conn.execute(
                "SELECT count(*) FROM episode_chunks WHERE project_id = ?;",
                (self.project_id,),
            )
            episode_chunks = cur.fetchone()[0]

            cur = conn.execute(
                "SELECT count(*) FROM tool_traces WHERE project_id = ?;",
                (self.project_id,),
            )
            trace_count = cur.fetchone()[0]

            cur = conn.execute(
                """
                SELECT (SELECT count(*) FROM facts WHERE project_id = ? AND status = 'active' AND embedded_at IS NULL)
                     + (SELECT count(*) FROM episode_chunks WHERE project_id = ? AND embedded_at IS NULL);
                """,
                (self.project_id, self.project_id),
            )
            pending_count = cur.fetchone()[0]

            vec_status = (
                "enabled"
                if self.embedding_client is not None
                else "disabled (install mem-plane[embed])"
            )
            db_path = self.settings.db_path if self.settings else "~/.mem-plane/memory.db"

            lines = [
                f"Project: {self.project_id}",
                f"Database: {db_path}",
                f"Vector search: {vec_status}",
                f"Active facts: {active_facts} (pinned: {pinned_facts})",
                f"Episode chunks: {episode_chunks}",
                f"Tool traces: {trace_count}",
                f"Pending embeddings: {pending_count}",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.exception("Error in memory_status")
            return f"error: {e}"
