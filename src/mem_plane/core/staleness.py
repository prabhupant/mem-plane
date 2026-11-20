"""File staleness checking for facts with associated paths."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem_plane.core.models import Fact

# Cache entry: (content_sha256, cached_at_monotonic)
_HASH_CACHE: dict[tuple[str, int, int], tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 10.0
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


def clear_staleness_cache() -> None:
    """Clear in-process file hash cache (used in tests)."""
    _HASH_CACHE.clear()


def _get_file_hash(path: Path) -> str | None:
    """Read and hash file contents with 10s mtime/size cache. Skips files > 5MB."""
    try:
        stat = path.stat()
    except OSError:
        return None

    if stat.st_size > _MAX_FILE_BYTES:
        return None

    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()

    cached = _HASH_CACHE.get(cache_key)
    if cached is not None:
        file_hash, cached_at = cached
        if now - cached_at < _CACHE_TTL_SECONDS:
            return file_hash

    try:
        content = path.read_bytes()
    except OSError:
        return None

    file_hash = hashlib.sha256(content).hexdigest()
    _HASH_CACHE[cache_key] = (file_hash, now)
    return file_hash


def check_fact_staleness(fact: Fact, project_root: str | Path | None) -> str | None:
    """Check if any file path associated with fact is deleted or changed.

    Returns:
        Staleness reason string (e.g. 'auth/config.py changed', 'src/app.py deleted'),
        or None if all paths are current or skipped.
    """
    if project_root is None or not fact.paths:
        return None

    root = Path(project_root)

    for fp in fact.paths:
        file_path = root / fp.rel_path

        if not file_path.exists() or not file_path.is_file():
            return f"{fp.rel_path} deleted"

        file_hash = _get_file_hash(file_path)
        # Skip files over 5 MB (where file_hash is None but file exists)
        if file_hash is None:
            continue

        if fp.content_sha256 and file_hash != fp.content_sha256:
            return f"{fp.rel_path} changed"

    return None
