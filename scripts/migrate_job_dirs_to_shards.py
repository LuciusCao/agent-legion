"""One-shot migration: move legacy flat job dirs into the sharded layout.

Legacy rows store ``storage_dir = jobs/<workspace>/<job_id>`` (3 segments);
the sharded layout adds a shard segment (4 segments). For each row this
script renames the on-disk directory (atomic same-filesystem rename, outside
any transaction) and then updates that row's ``jobs.storage_dir`` immediately,
so the window where filesystem and DB disagree is a single job for a few
milliseconds. ``--apply`` must still be run with the backend and workers
stopped: an online writer resolving the legacy path mid-rename could
recreate the old directory and split one job's files across both layouts.

Safe to interrupt and re-run: already-migrated rows are skipped (4-segment
``storage_dir``), and a row whose dir was renamed but whose DB update was
missed (crash between the two steps) self-heals — the new path is detected
and only the DB update is applied. An empty shard dir left behind by a
resubmitted legacy job (the old create-then-conflict path) is removed and
migration proceeds. Rows where both paths exist with real content are
reported as conflicts and never overwritten.

Usage:
    uv run python -m scripts.migrate_job_dirs_to_shards [--apply] [--database-url ...]
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

from server.app.jobs import JobQueries
from server.app.jobs.storage_layout import job_storage_dir
from server.app.settings import load_settings
from server.app.storage_paths import make_data_relative

_BATCH_SIZE = 500


@dataclass
class MigrationStats:
    migrated: int = 0
    already_sharded: int = 0
    missing_dir: int = 0
    healed_db_only: int = 0
    removed_empty_stub: int = 0
    conflicts: list[str] = field(default_factory=list)


def _iter_legacy_rows(db: JobQueries):
    """Yield legacy-layout jobs rows, keyset-paginated on the primary key."""
    last_id = ""
    while True:
        with db._connect_read() as conn:
            rows = conn.execute(
                """
                select id, workspace_id, storage_dir from jobs
                where id > %s
                order by id limit %s
                """,
                (last_id, _BATCH_SIZE),
            ).fetchall()
        if not rows:
            return
        yield from rows
        last_id = str(rows[-1]["id"])
        if len(rows) < _BATCH_SIZE:
            return


def _is_legacy(storage_dir: str) -> bool:
    return len(Path(storage_dir).parts) == 3


def _update_storage_dir(db: JobQueries, job_id: str, new_rel: str) -> None:
    with db.connect() as conn:
        conn.execute("update jobs set storage_dir = %s where id = %s", (new_rel, job_id))


def _is_empty_dir(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def migrate_job_dirs(db: JobQueries, data_dir: Path, *, apply: bool) -> MigrationStats:
    """Migrate legacy flat job dirs to the sharded layout.

    Returns per-category counters; with ``apply=False`` nothing is renamed or
    written, only classified.
    """
    stats = MigrationStats()
    jobs_dir = data_dir / "jobs"
    for row in _iter_legacy_rows(db):
        job_id = str(row["id"])
        storage_dir = row["storage_dir"] or ""
        if not storage_dir or not _is_legacy(storage_dir):
            stats.already_sharded += 1
            continue
        workspace_id = str(row["workspace_id"])
        old_abs = data_dir / storage_dir
        new_abs = job_storage_dir(jobs_dir, workspace_id, job_id)
        new_rel = make_data_relative(new_abs, data_dir)
        # An empty shard dir is a stub left by a resubmitted legacy job; it is
        # safe to remove. Both paths with real content is a true conflict.
        empty_stub = _is_empty_dir(new_abs)
        if old_abs.is_dir() and new_abs.exists() and not empty_stub:
            stats.conflicts.append(job_id)
            continue
        if not old_abs.is_dir() and not new_abs.exists():
            stats.missing_dir += 1
            continue
        if not old_abs.is_dir():
            # Rename landed but the DB update did not (interrupted run).
            stats.healed_db_only += 1
        else:
            stats.migrated += 1
        if apply:
            if old_abs.is_dir():
                if empty_stub:
                    new_abs.rmdir()
                    stats.removed_empty_stub += 1
                new_abs.parent.mkdir(parents=True, exist_ok=True)
                os.rename(old_abs, new_abs)
            _update_storage_dir(db, job_id, new_rel)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the migration (default: dry-run classification only).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured AGENT_LEGION_DATABASE_URL.",
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    db = JobQueries(dsn, settings.data_dir / "jobs")
    stats = migrate_job_dirs(db, settings.data_dir, apply=args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] migrated={stats.migrated} already_sharded={stats.already_sharded} "
        f"healed_db_only={stats.healed_db_only} missing_dir={stats.missing_dir} "
        f"removed_empty_stub={stats.removed_empty_stub} conflicts={len(stats.conflicts)}"
    )
    if stats.conflicts:
        print("conflicting job ids (both paths exist, skipped):")
        for job_id in stats.conflicts[:20]:
            print(f"  {job_id}")
        if len(stats.conflicts) > 20:
            print(f"  ... and {len(stats.conflicts) - 20} more")


if __name__ == "__main__":
    main()
