"""One-shot rewrite of legacy absolute database paths to relative paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger("server.app.services.path_hygiene")

_MIGRATE_CHUNK_ROWS = 500
_MANAGED_CATEGORIES = ("videos", "jobs", "logs", "packages")


def _rebase_legacy_absolute(stored_path: str, data_dir_name: str) -> str | None:
    """Map one legacy absolute path to its data-dir-relative form, or None."""
    parts = Path(stored_path).parts
    for i, part in enumerate(parts):
        if part == data_dir_name and i + 1 < len(parts) and parts[i + 1] in _MANAGED_CATEGORIES:
            return "/".join(parts[i + 1 :])
    return None


def migrate_absolute_db_paths(
    db: JobQueries, data_dir: Path, *, chunk_rows: int = _MIGRATE_CHUNK_ROWS
) -> dict[str, int]:
    """Rewrite legacy rows in bounded chunks; return per-column write counts."""
    from server.app.jobs.queries.path_hygiene import PATH_HYGIENE_COLUMNS

    data_dir_name = data_dir.resolve(strict=True).name
    migrated: dict[str, int] = {}
    for table, column, key in PATH_HYGIENE_COLUMNS:
        done = 0
        cursor: str | None = None
        while True:
            rows = db.fetch_absolute_path_chunk(table, column, key, chunk_rows, after=cursor)
            if not rows:
                break
            updates = []
            for row in rows:
                relative = _rebase_legacy_absolute(row["value"], data_dir_name)
                if relative is not None:
                    updates.append((relative, row["key"], row["value"]))
            if updates:
                db.rewrite_path_rows(table, column, key, updates)
                done += len(updates)
            if len(rows) < chunk_rows:
                break
            cursor = rows[-1]["key"]
        if done:
            migrated[f"{table}.{column}"] = done
    if migrated:
        logger.info(
            "path-hygiene one-time rewrite: legacy absolute rows rebased to data-dir-relative (%s)",
            ", ".join(f"{name}={count}" for name, count in sorted(migrated.items())),
        )
    return migrated
