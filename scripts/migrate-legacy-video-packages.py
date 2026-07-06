#!/usr/bin/env python3
# ruff: noqa: E402
"""Migrate legacy Video Hive packages into the workspace_packages table.

Run:
    UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-legacy-video-packages.py
    UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-legacy-video-packages.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.settings import load_settings
from server.app.storage_paths import make_data_relative, resolve_data_path

WORKSPACE_ID = "video_knowledge"
LEGACY_PREFIX = "video-hive-"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"video_hive-before-legacy-package-migration-{_timestamp()}.sqlite"
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate(dry_run: bool = False) -> list[dict[str, str]]:
    settings = load_settings()
    db_path = settings.data_dir / "video_hive.sqlite"
    packages_dir = settings.packages_dir
    data_dir = settings.data_dir

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    backup_path = _backup_db(db_path)
    print(f"Backed up database to {backup_path}")

    workspace_packages_dir = packages_dir / f"workspace-{WORKSPACE_ID}"
    if not dry_run:
        workspace_packages_dir.mkdir(parents=True, exist_ok=True)

    migrated: list[dict[str, str]] = []

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, path, video_count, size_bytes, created_at "
            "FROM packages WHERE path LIKE ? ORDER BY created_at",
            (f"%{LEGACY_PREFIX}%",),
        ).fetchall()

        for row in rows:
            legacy_id = str(row["id"])
            name = str(row["name"] or "")
            stored_path = str(row["path"] or "")
            video_count = int(row["video_count"] or 0)
            size_bytes = int(row["size_bytes"] or 0)
            created_at = str(row["created_at"] or "")

            if not stored_path:
                print(f"Skipping legacy package {legacy_id}: empty path")
                continue

            try:
                source_path = resolve_data_path(stored_path, data_dir, allow_missing=False)
            except Exception as exc:
                print(f"Skipping legacy package {legacy_id}: cannot resolve {stored_path}: {exc}")
                continue

            if not source_path.name.startswith(LEGACY_PREFIX) or not source_path.is_file():
                print(f"Skipping legacy package {legacy_id}: not a video-hive zip ({source_path})")
                continue

            target_path = workspace_packages_dir / source_path.name
            relative_path = make_data_relative(target_path, data_dir)

            existing = conn.execute(
                "SELECT 1 FROM workspace_packages WHERE workspace_id = ? AND path = ?",
                (WORKSPACE_ID, relative_path),
            ).fetchone()
            if existing is not None:
                print(f"Skipping {source_path.name}: already migrated")
                continue

            if dry_run:
                print(
                    f"[dry-run] Would migrate {source_path.name} to {relative_path} "
                    f"(name={name!r}, videos={video_count})"
                )
                migrated.append(
                    {
                        "filename": source_path.name,
                        "name": name,
                        "video_count": str(video_count),
                        "size_bytes": str(size_bytes),
                    }
                )
                continue

            if not target_path.exists():
                shutil.copy2(source_path, target_path)
                print(f"Copied {source_path.name} to {target_path}")
            else:
                print(f"Target already exists: {target_path.name}")

            actual_size = target_path.stat().st_size
            cursor = conn.execute(
                """
                INSERT INTO workspace_packages(
                    workspace_id, path, name, job_count, size_bytes, locked, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    WORKSPACE_ID,
                    relative_path,
                    name,
                    video_count,
                    actual_size,
                    0,
                    created_at or datetime.now(UTC).isoformat(),
                ),
            )
            conn.execute("DELETE FROM packages WHERE id = ?", (legacy_id,))
            conn.commit()
            migrated.append(
                {
                    "filename": source_path.name,
                    "workspace_package_id": str(cursor.lastrowid or 0),
                    "name": name,
                    "video_count": str(video_count),
                    "size_bytes": str(actual_size),
                }
            )
            print(f"Migrated {source_path.name} -> workspace_packages id={cursor.lastrowid}")

    return migrated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Video Hive packages into the video_knowledge workspace package list."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be migrated without changing data."
    )
    args = parser.parse_args()

    try:
        migrated = migrate(dry_run=args.dry_run)
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nMigrated {len(migrated)} package(s)")
    for item in migrated:
        print(
            f"  - {item['filename']}: {item.get('name', '')} ({item.get('video_count', '?')} videos)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
