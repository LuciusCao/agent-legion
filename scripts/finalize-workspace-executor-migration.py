#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Make the project root importable when the script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.db.migrations.report import MigrationBlockedError
from server.app.executors.backup import legacy_backup_path
from server.app.executors.config import ExecutorConfig
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.registry import list_registered_pipelines
from server.app.settings import load_settings

_EMPTY_REPORT_JSON = json.dumps(
    {
        "migration_version": 5,
        "migration_name": "remove_legacy_executor_paths",
        "issues": [],
    },
    sort_keys=True,
    separators=(",", ":"),
)


def _check(
    db_path: Path, definitions: list[PipelineDefinition], executors: dict[str, ExecutorConfig]
) -> int:
    if not db_path.exists():
        print(_EMPTY_REPORT_JSON)
        return 0

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            finalize_legacy_executor_schema(conn, definitions, executors, dry_run=True)
        except MigrationBlockedError as exc:
            print(exc.report.to_json())
            return 1
    print(_EMPTY_REPORT_JSON)
    return 0


def _apply(
    db_path: Path, definitions: list[PipelineDefinition], executors: dict[str, ExecutorConfig]
) -> int:
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    jobs_dir = db_path.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_db = JobQueries(db_path, jobs_dir)
    backup_path = legacy_backup_path(db_path)

    with job_db.connect() as conn:
        try:
            finalize_legacy_executor_schema(conn, definitions, executors, backup_path=backup_path)
        except MigrationBlockedError as exc:
            print(exc.report.to_json(), file=sys.stderr)
            return 1

    if backup_path.exists():
        print(f"Backup created: {backup_path}")
    print("Workspace executor migration finalized.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Finalize legacy Workspace Agent assignments into Executor allocations."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Dry-run JSON report, no writes.")
    group.add_argument("--apply", action="store_true", help="Apply the finalization.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Video Hive data directory (defaults to config/data_dir).",
    )
    args = parser.parse_args(argv)

    settings = load_settings(data_dir=args.data_dir)
    db_path = settings.data_dir / "video_hive.sqlite"
    definitions = list_registered_pipelines(settings.root_dir)
    executors = settings.executor_definitions

    if args.check:
        return _check(db_path, definitions, executors)
    return _apply(db_path, definitions, executors)


if __name__ == "__main__":
    sys.exit(main())
