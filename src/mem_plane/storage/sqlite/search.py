"""Hybrid search engine implementing FTS5 keyword, sqlite-vec vector, and weighted RRF fusion."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mem_plane.core.models import SearchHit
from mem_plane.core.rrf import weighted_rrf
from mem_plane.core.staleness import check_fact_staleness

if TYPE_CHECKING:
    import sqlean.dbapi2 as sqlite3

    from mem_plane.config import Settings
    from mem_plane.storage.protocols import SearchRequest


def build_fts5_query(query: str, max_tokens: int = 24) -> str:
    """Build an FTS5 MATCH query string from freeform user input (Section 7.2).

    1. Split on whitespace, drop empty tokens, cap at max_tokens.
    2. Strip FTS5 syntax characters: ", (, ), *, :, ^, {, }
       Keep _, -, ., / inside tokens.
    3. Wrap each token in double quotes for phrase tokenization.
    4. Join with ' OR '.
    """
    raw_tokens = [t.strip() for t in query.split() if t.strip()]
    if not raw_tokens:
        return ""

    tokens = raw_tokens[:max_tokens]
    cleaned: list[str] = []

    for token in tokens:
        clean = re.sub(r"[\"()*:^{}]", "", token).strip()
        if clean:
            cleaned.append(f'"{clean}"')

    if not cleaned:
        return ""

    return " OR ".join(cleaned)


def search_facts_keyword(
    conn: sqlite3.Connection,
    project_id: str,
    fts_query: str,
    n: int,
    include_superseded: bool = False,
    kinds: list[str] | None = None,
) -> list[str]:
    """Execute FTS5 keyword search on facts table with column BM25 weights."""
    if not fts_query:
        return []

    status_filter = (
        "f.status IN ('active', 'superseded')" if include_superseded else "f.status = 'active'"
    )

    query_parts = [
        """
        SELECT f.fact_id, bm25(facts_fts, 0.0, 0.0, 2.0, 1.0) AS rank
        FROM facts_fts
        JOIN facts f ON f.fact_id = facts_fts.fact_id
        WHERE facts_fts MATCH ?
          AND facts_fts.project_id = ?
        """,
        f"AND {status_filter}",
    ]
    params: list[Any] = [fts_query, project_id]

    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        query_parts.append(f"AND f.kind IN ({placeholders})")
        params.extend(kinds)

    query_parts.append("ORDER BY rank ASC, f.fact_id ASC LIMIT ?;")
    params.append(n)

    sql = "\n".join(query_parts)
    cur = conn.execute(sql, params)
    return [row[0] for row in cur.fetchall()]


def search_episodes_keyword(
    conn: sqlite3.Connection,
    project_id: str,
    fts_query: str,
    n: int,
) -> list[str]:
    """Execute FTS5 keyword search on episode_chunks with BM25 weights."""
    if not fts_query:
        return []

    sql = """
    SELECT c.chunk_id, bm25(episode_fts, 0.0, 0.0, 1.0) AS rank
    FROM episode_fts
    JOIN episode_chunks c ON c.chunk_id = episode_fts.chunk_id
    WHERE episode_fts MATCH ?
      AND episode_fts.project_id = ?
    ORDER BY rank ASC, c.chunk_id ASC
    LIMIT ?;
    """
    cur = conn.execute(sql, (fts_query, project_id, n))
    return [row[0] for row in cur.fetchall()]


def search_facts_vector(
    conn: sqlite3.Connection,
    project_id: str,
    query_vector: list[float] | None,
    n: int,
    partition_keys: bool = True,
    include_superseded: bool = False,
    kinds: list[str] | None = None,
    max_cosine_distance: float = 0.55,
) -> list[str]:
    """Execute vector search on facts_vec with distance cut-off and status filter."""
    if query_vector is None:
        return []

    status_filter = (
        "status IN ('active', 'superseded')" if include_superseded else "status = 'active'"
    )
    vec_repr = str(query_vector)

    if partition_keys:
        sql = f"""
        SELECT fact_id, distance
        FROM facts_vec
        WHERE embedding MATCH ?
          AND k = ?
          AND project_id = ?
          AND {status_filter}
        ORDER BY distance ASC;
        """
        cur = conn.execute(sql, (vec_repr, n, project_id))
        candidates: list[tuple[str, float]] = [
            (row[0], float(row[1]))
            for row in cur.fetchall()
            if float(row[1]) <= max_cosine_distance
        ]
    else:
        # Fallback table has no partition key or status column
        k_val = n * 5
        sql = """
        SELECT v.fact_id, v.distance
        FROM facts_vec v
        JOIN facts f ON f.fact_id = v.fact_id
        WHERE v.embedding MATCH ?
          AND v.k = ?
          AND f.project_id = ?
        """
        cur = conn.execute(sql, (vec_repr, k_val, project_id))
        candidates = [
            (row[0], float(row[1]))
            for row in cur.fetchall()
            if float(row[1]) <= max_cosine_distance
        ]

    if not candidates:
        return []

    # If kinds filter is active, verify kind against facts table
    fact_ids = [cid for cid, _ in candidates]
    if kinds:
        placeholders = ",".join("?" for _ in fact_ids)
        kind_placeholders = ",".join("?" for _ in kinds)
        sql_check = f"""
        SELECT fact_id FROM facts
        WHERE fact_id IN ({placeholders}) AND kind IN ({kind_placeholders});
        """
        params = list(fact_ids) + list(kinds)
        valid_ids = {r[0] for r in conn.execute(sql_check, params).fetchall()}
        return [cid for cid in fact_ids if cid in valid_ids]

    return fact_ids


def search_episodes_vector(
    conn: sqlite3.Connection,
    project_id: str,
    query_vector: list[float] | None,
    n: int,
    partition_keys: bool = True,
    max_cosine_distance: float = 0.55,
) -> list[str]:
    """Execute vector search on episode_vec with distance cut-off."""
    if query_vector is None:
        return []

    vec_repr = str(query_vector)

    if partition_keys:
        sql = """
        SELECT chunk_id, distance
        FROM episode_vec
        WHERE embedding MATCH ?
          AND k = ?
          AND project_id = ?
        ORDER BY distance ASC;
        """
        cur = conn.execute(sql, (vec_repr, n, project_id))
        return [row[0] for row in cur.fetchall() if float(row[1]) <= max_cosine_distance]
    else:
        k_val = n * 5
        sql = """
        SELECT v.chunk_id, v.distance
        FROM episode_vec v
        JOIN episode_chunks c ON c.chunk_id = v.chunk_id
        WHERE v.embedding MATCH ?
          AND v.k = ?
          AND c.project_id = ?
        ORDER BY v.distance ASC;
        """
        cur = conn.execute(sql, (vec_repr, k_val, project_id))
        return [row[0] for row in cur.fetchall() if float(row[1]) <= max_cosine_distance]


def execute_hybrid_search(
    conn: sqlite3.Connection,
    req: SearchRequest,
    query_vector: list[float] | None,
    partition_keys: bool,
    settings: Settings,
    project_root: str | None = None,
) -> list[SearchHit]:
    """Execute 4-list hybrid retrieval with weighted RRF fusion and hydration."""
    top_k = req.effective_limit
    candidate_n = max(top_k * 4, 20)

    fts_query = build_fts5_query(req.query)
    kinds_list = [k.value for k in req.effective_kinds] if req.effective_kinds else None

    # 1. Fact keyword hits
    fact_kw = search_facts_keyword(
        conn,
        project_id=req.project_id,
        fts_query=fts_query,
        n=candidate_n,
        include_superseded=req.include_superseded,
        kinds=kinds_list,
    )

    # 2. Fact vector hits
    fact_vec = search_facts_vector(
        conn,
        project_id=req.project_id,
        query_vector=query_vector,
        n=candidate_n,
        partition_keys=partition_keys,
        include_superseded=req.include_superseded,
        kinds=kinds_list,
        max_cosine_distance=settings.max_cosine_distance,
    )

    # 3. Episode keyword hits
    ep_kw: list[str] = []
    ep_vec: list[str] = []
    if req.include_episodes:
        ep_kw = search_episodes_keyword(
            conn,
            project_id=req.project_id,
            fts_query=fts_query,
            n=candidate_n,
        )
        # 4. Episode vector hits
        ep_vec = search_episodes_vector(
            conn,
            project_id=req.project_id,
            query_vector=query_vector,
            n=candidate_n,
            partition_keys=partition_keys,
            max_cosine_distance=settings.max_cosine_distance,
        )

    # 4-list fusion with configured weights
    fused = weighted_rrf(
        [
            (fact_kw, settings.weight_fact_keyword),
            (fact_vec, settings.weight_fact_vector),
            (ep_kw, settings.weight_episode_keyword),
            (ep_vec, settings.weight_episode_vector),
        ],
        k=settings.rrf_k,
        top_n=top_k,
    )

    if not fused:
        return []

    # Hydrate hits
    results: list[SearchHit] = []
    for ref_id, score in fused:
        if ref_id.startswith("f_"):
            cur = conn.execute(
                """
                SELECT fact_id, summary, kind, pinned, created_at, status
                FROM facts
                WHERE fact_id = ? AND project_id = ?;
                """,
                (ref_id, req.project_id),
            )
            row = cur.fetchone()
            if not row:
                continue

            # Check paths for staleness
            from mem_plane.core.models import Fact, FactKind, FactPath, FactStatus

            cur_p = conn.execute(
                "SELECT rel_path, content_sha256, git_commit FROM fact_paths WHERE fact_id = ?;",
                (ref_id,),
            )
            paths = [
                FactPath(rel_path=pr[0], content_sha256=pr[1], git_commit=pr[2])
                for pr in cur_p.fetchall()
            ]
            fact_obj = Fact(
                fact_id=row[0],
                project_id=req.project_id,
                kind=FactKind(row[2]),
                summary=row[1],
                body="",
                status=FactStatus(row[5]),
                pinned=bool(row[3]),
                paths=paths,
                created_at=datetime.fromisoformat(row[4]),
            )
            stale_reason = check_fact_staleness(fact_obj, project_root)

            results.append(
                SearchHit(
                    ref=row[0],
                    ref_type="fact",
                    summary=row[1],
                    score=score,
                    kind=row[2],
                    stale=stale_reason is not None,
                    stale_reason=stale_reason,
                    pinned=bool(row[3]),
                    created_at=datetime.fromisoformat(row[4]),
                )
            )

        elif ref_id.startswith("e_"):
            cur = conn.execute(
                """
                SELECT chunk_id, text, created_at
                FROM episode_chunks
                WHERE chunk_id = ? AND project_id = ?;
                """,
                (ref_id, req.project_id),
            )
            row = cur.fetchone()
            if not row:
                continue

            # First 200 chars of chunk text
            chunk_text = row[1].strip()
            summary = chunk_text[:200]

            results.append(
                SearchHit(
                    ref=row[0],
                    ref_type="episode",
                    summary=summary,
                    score=score,
                    kind=None,
                    stale=False,
                    stale_reason=None,
                    pinned=False,
                    created_at=datetime.fromisoformat(row[2]) if row[2] else datetime.now(UTC),
                )
            )

    return results
