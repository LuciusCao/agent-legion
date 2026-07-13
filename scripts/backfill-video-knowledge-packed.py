#!/usr/bin/env python3
# ruff: noqa: E402
"""Backfill jobs.packed for video_knowledge jobs that were in migrated legacy packages.

Run:
    UV_CACHE_DIR=.uv-cache uv run python scripts/backfill-video-knowledge-packed.py
    UV_CACHE_DIR=.uv-cache uv run python scripts/backfill-video-knowledge-packed.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.settings import load_settings

WORKSPACE_ID = "video_knowledge"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"video_hive-before-backfill-packed-{_timestamp()}.sqlite"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _legacy_video_ids_from_package(package_path: Path) -> set[str]:
    with zipfile.ZipFile(package_path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    return {str(video["id"]) for video in manifest.get("videos", [])}


def backfill(dry_run: bool = False) -> int:
    settings = load_settings()
    db_path = settings.data_dir / "video_hive.sqlite"

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    workspace_packages_dir = settings.packages_dir / f"workspace-{WORKSPACE_ID}"
    package_paths = sorted(workspace_packages_dir.glob("video-hive-*.zip"))
    if not package_paths:
        print("No migrated video-hive packages found; nothing to backfill.")
        return 0

    legacy_ids: set[str] = set()
    for package_path in package_paths:
        legacy_ids.update(_legacy_video_ids_from_package(package_path))
    print(f"Found {len(legacy_ids)} legacy video ids across {len(package_paths)} package(s)")

    backup_path = _backup_db(db_path)
    print(f"Backed up database to {backup_path}")

    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, storage_dir FROM jobs WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        ).fetchall()

        job_ids_to_mark: list[str] = []
        for row in rows:
            job_id = str(row["id"])
            storage_dir = str(row["storage_dir"] or "")
            if not storage_dir:
                continue
            job_dir = Path(settings.data_dir) / storage_dir
            video_input_path = job_dir / "video_input.json"
            if not video_input_path.is_file():
                continue
            try:
                video_input = json.loads(video_input_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            legacy_video_id = str(video_input.get("legacy_video_id") or "")
            if legacy_video_id in legacy_ids:
                job_ids_to_mark.append(job_id)

        print(f"Matched {len(job_ids_to_mark)} current jobs to migrated packages")
        if dry_run:
            print("[dry-run] would mark matched jobs as packed=1")
            return len(job_ids_to_mark)

        if not job_ids_to_mark:
            return 0

        placeholders = ",".join("?" for _ in job_ids_to_mark)
        conn.execute(
            f"UPDATE jobs SET packed = 1 WHERE id IN ({placeholders})",
            job_ids_to_mark,
        )
        conn.commit()
        print(f"Marked {len(job_ids_to_mark)} jobs as packed=1")

    return len(job_ids_to_mark)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill packed status for video_knowledge jobs in migrated legacy packages."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be updated without changing data."
    )
    args = parser.parse_args()

    try:
        count = backfill(dry_run=args.dry_run)
    except Exception as exc:
        print(f"Backfill failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone. Affected jobs: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
