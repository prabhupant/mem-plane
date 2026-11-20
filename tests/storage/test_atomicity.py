"""Tests for atomic transaction rollbacks on failure."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from mem_plane.core.models import Fact, FactKind
from mem_plane.storage.sqlite.store import SQLiteStore


class FaultyConnectionProxy:
    """Wrapper that raises on specific queries to test atomicity."""

    def __init__(self, target: Any) -> None:
        self._target = target

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO facts_fts" in sql:
            raise RuntimeError("Simulated crash before FTS insertion")
        return self._target.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def __enter__(self) -> Any:
        return self._target.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Any:
        return self._target.__exit__(exc_type, exc_val, exc_tb)


def test_atomicity_fact_insert_failure_rollback(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "atomicity.db")
    try:
        fact = Fact(
            fact_id="f_atomic000001",
            project_id="p_atomic_proj",
            kind=FactKind.DECISION,
            summary="Test atomicity failure.",
            body="This should not be persisted if FTS insertion fails.",
        )

        real_conn = store.conn
        store.conn = FaultyConnectionProxy(real_conn)

        with pytest.raises(RuntimeError, match="Simulated crash before FTS insertion"):
            store.create_fact(fact)

        store.conn = real_conn

        # Verify nothing was written to facts, fact_history, or facts_fts
        cur_f = store.conn.execute("SELECT count(*) FROM facts WHERE fact_id = 'f_atomic000001';")
        assert cur_f.fetchone()[0] == 0

        cur_h = store.conn.execute(
            "SELECT count(*) FROM fact_history WHERE fact_id = 'f_atomic000001';"
        )
        assert cur_h.fetchone()[0] == 0

        cur_fts = store.conn.execute(
            "SELECT count(*) FROM facts_fts WHERE fact_id = 'f_atomic000001';"
        )
        assert cur_fts.fetchone()[0] == 0

        assert store.get_fact("f_atomic000001", "p_atomic_proj") is None
    finally:
        store.close()
