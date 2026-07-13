#!/usr/bin/env python3
# ruff: noqa: E402
"""Migrate legacy Video Hive knowledge videos into Agent Legion workspace jobs."""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from contextlib import closing
from pathlib import Path

# Make the project root importable when the script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrate_video_hive_to_agent_legion_core import (
    LEGACY_PHASES,
    PHASE_TO_NODE,
    VIDEO_NODES,
    WORKFLOW_KEY,
    WORKSPACE_ID,
    Environment,
    MigrationError,
    MigrationReport,
    VideoMapping,
    _ensure_canonical_source_mp4,
    _ensure_workspace,
    _job_status_from_nodes,
    _list_legacy_videos,
    _list_phase_runs,
    _list_transcription_runs,
    _map_node_statuses,
    _missing_completed_artifacts,
    _remove_created_dirs,
    _source_id_for_video,
    _timestamp,
    _video_input_for_video,
    _workflow_definition,
    _write_package_manifest,
    job_id_for_video,
)
from server.app.db.connection import connect_sqlite
from server.app.jobs import JobQueries
from server.app.jobs.executor_configuration import (
    mark_workspace_executor_configuration_authoritative,
)
from server.app.storage_paths import ManagedPathError, make_data_relative, resolve_video_dir


def preflight(env: Environment) -> MigrationReport:
    """Validate that the legacy database can be migrated without collisions."""
    errors: list[MigrationError] = []
    mappings: list[VideoMapping] = []

    try:
        _workflow_definition(env)
    except (KeyError, FileNotFoundError) as exc:
        errors.append(
            MigrationError(
                legacy_video_id="",
                message=f"Workflow '{WORKFLOW_KEY}' cannot be loaded: {exc}",
            )
        )

    queries = JobQueries(env.db_path, env.jobs_dir)

    with closing(connect_sqlite(env.db_path)) as conn, conn:
        videos = _list_legacy_videos(conn)

    job_id_to_video_id: dict[str, str] = {}
    for video in videos:
        video_id = str(video["id"])
        content_type = str(video.get("content_type") or "")
        status = str(video.get("status") or "")
        current_phase = str(video.get("current_phase") or "")
        storage_dir = str(video.get("storage_dir") or "").strip()
        job_id = job_id_for_video(video)

        if previous_video_id := job_id_to_video_id.get(job_id):
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message=(
                        f"duplicate target job id: {job_id} (also used by {previous_video_id})"
                    ),
                )
            )
        else:
            job_id_to_video_id[job_id] = video_id

        if content_type != "knowledge":
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message=f"content_type '{content_type}' is not supported; only 'knowledge' videos are migrated",
                )
            )

        if status == "running":
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message="status 'running' must be resolved before migration",
                )
            )

        if current_phase not in LEGACY_PHASES:
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message=f"current_phase '{current_phase}' is not a recognized legacy phase",
                )
            )

        if status == "completed" or storage_dir:
            try:
                source_dir = resolve_video_dir(video, env.videos_dir)
            except ManagedPathError as exc:
                errors.append(
                    MigrationError(
                        legacy_video_id=video_id,
                        message=f"source video directory is invalid: {exc}",
                    )
                )
                source_dir = None
            if source_dir is not None and not source_dir.is_dir():
                errors.append(
                    MigrationError(
                        legacy_video_id=video_id,
                        message=f"source video directory does not exist: {source_dir}",
                    )
                )
            if source_dir is not None and source_dir.is_dir() and status == "completed":
                missing = _missing_completed_artifacts(video, source_dir)
                if missing:
                    errors.append(
                        MigrationError(
                            legacy_video_id=video_id,
                            message=f"missing completed artifact(s): {', '.join(missing)}",
                        )
                    )

        if queries.get_job(job_id) is not None:
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message=f"target job id already exists: {job_id}",
                )
            )

        target_dir = env.jobs_dir / job_id
        if target_dir.exists():
            errors.append(
                MigrationError(
                    legacy_video_id=video_id,
                    message=f"target job directory already exists: {target_dir}",
                )
            )

        mappings.append(
            VideoMapping(
                legacy_video_id=video_id,
                job_id=job_id,
                source_id=_source_id_for_video(video),
                title=str(video.get("title") or "").strip(),
            )
        )

    return MigrationReport(
        blocked=bool(errors),
        mappings=mappings,
        errors=errors,
        created_paths=[],
    )


def apply_migration(env: Environment) -> MigrationReport:
    """Migrate legacy knowledge videos into workspace jobs."""
    preflight_report = preflight(env)
    if preflight_report.blocked:
        return preflight_report

    env.backup_dir.mkdir(parents=True, exist_ok=True)
    env.jobs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _timestamp()
    backup_path = env.backup_dir / f"video-hive-before-agent-legion-{timestamp}.sqlite"
    shutil.copy2(env.db_path, backup_path)

    created_paths: list[str] = [str(backup_path)]
    mappings: list[VideoMapping] = []
    copied_job_dirs: list[Path] = []

    with closing(connect_sqlite(env.db_path)) as conn, conn:
        videos = _list_legacy_videos(conn)

    try:
        for video in videos:
            video_id = str(video["id"])
            job_id = job_id_for_video(video)
            status = str(video.get("status") or "")
            source_dir = resolve_video_dir(video, env.videos_dir)
            job_dir = env.jobs_dir / job_id

            if source_dir.is_dir():
                shutil.copytree(source_dir, job_dir)
            else:
                job_dir.mkdir(parents=True, exist_ok=True)
            copied_job_dirs.append(job_dir)
            _ensure_canonical_source_mp4(video, job_dir)

            (job_dir / "video_input.json").write_text(
                json.dumps(_video_input_for_video(video), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if status == "completed":
                _write_package_manifest(job_dir)

            with closing(connect_sqlite(env.db_path)) as conn, conn:
                transcription_runs = _list_transcription_runs(conn, video_id)
            (job_dir / "transcription_runs.json").write_text(
                json.dumps(transcription_runs, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    except Exception:
        _remove_created_dirs(copied_job_dirs)
        raise

    queries = JobQueries(env.db_path, env.jobs_dir)
    try:
        with queries.connect() as conn:
            _ensure_workspace(conn)
            for executor_id, limit in (("local-default", 16), ("pi", 8)):
                conn.execute(
                    """
                    insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
                    values (?, ?, ?)
                    on conflict(workspace_id, executor_id) do nothing
                    """,
                    (WORKSPACE_ID, executor_id, limit),
                )
            for node_key in ("download", "transcribe", "assemble", "package"):
                conn.execute(
                    """
                    insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)
                    values (?, ?, ?, ?)
                    on conflict(workspace_id, workflow_key, node_key) do nothing
                    """,
                    (WORKSPACE_ID, WORKFLOW_KEY, node_key, "local-default"),
                )
            for node_key in (
                "subtitle_review",
                "chapter_generate",
                "interaction_generate",
                "content_review",
            ):
                conn.execute(
                    """
                    insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)
                    values (?, ?, ?, ?)
                    on conflict(workspace_id, workflow_key, node_key) do nothing
                    """,
                    (WORKSPACE_ID, WORKFLOW_KEY, node_key, "pi"),
                )
            mark_workspace_executor_configuration_authoritative(conn, WORKSPACE_ID)

            for video in videos:
                video_id = str(video["id"])
                job_id = job_id_for_video(video)
                source_id = _source_id_for_video(video)
                title = str(video.get("title") or source_id or job_id).strip()
                current_phase = str(video.get("current_phase") or "")
                status = str(video.get("status") or "")
                job_dir = env.jobs_dir / job_id
                node_statuses = _map_node_statuses(status, current_phase)

                if status == "completed":
                    node_statuses["package"] = "completed"

                job_status = _job_status_from_nodes(node_statuses)
                storage_dir_rel = make_data_relative(job_dir, env.data_dir)

                conn.execute(
                    """
                    insert into jobs(
                      id, workspace_id, workflow_key, source_type, source_id,
                      batch_id, title, status, storage_dir
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        WORKSPACE_ID,
                        WORKFLOW_KEY,
                        "video",
                        source_id,
                        "",
                        title,
                        job_status,
                        storage_dir_rel,
                    ),
                )
                for node_key in VIDEO_NODES:
                    conn.execute(
                        """
                        insert into job_nodes(job_id, node_key, status, created_at)
                        values (?, ?, ?, current_timestamp)
                        """,
                        (job_id, node_key, node_statuses[node_key]),
                    )

                phase_runs = _list_phase_runs(conn, video_id)
                for run in phase_runs:
                    phase_key = str(run.get("phase_key") or "")
                    if phase_key not in PHASE_TO_NODE:
                        continue
                    node_key = PHASE_TO_NODE[phase_key]
                    conn.execute(
                        """
                        insert into node_runs(
                          job_id, node_key, status, started_at, finished_at,
                          command_json, exit_code, log_path, error_message, run_dir, session_dir
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            node_key,
                            str(run.get("status") or ""),
                            run.get("started_at"),
                            run.get("finished_at"),
                            str(run.get("command_json") or "[]"),
                            run.get("exit_code"),
                            str(run.get("log_path") or ""),
                            str(run.get("error_message") or ""),
                            "",
                            "",
                        ),
                    )

                created_paths.append(str(job_dir))
                mappings.append(
                    VideoMapping(
                        legacy_video_id=video_id,
                        job_id=job_id,
                        source_id=source_id,
                        title=title,
                    )
                )
    except Exception:
        _remove_created_dirs(copied_job_dirs)
        raise

    report_path = env.backup_dir / f"video-hive-to-agent-legion-report-{timestamp}.json"
    report = MigrationReport(
        blocked=False,
        mappings=mappings,
        errors=[],
        created_paths=created_paths,
    )
    report_path.write_text(
        json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report.created_paths.append(str(report_path))

    return report


def _render_report(report: MigrationReport) -> str:
    lines: list[str] = []
    if report.blocked:
        lines.append("STATUS: BLOCKED")
        lines.append(f"Errors: {len(report.errors)}")
        for error in report.errors:
            lines.append(f"  [{error.legacy_video_id or '-'}] {error.message}")
    else:
        lines.append("STATUS: OK")
        lines.append(f"Migrated videos: {len(report.mappings)}")
        for mapping in report.mappings:
            lines.append(f"  {mapping.legacy_video_id} -> {mapping.job_id} ({mapping.title})")
        if report.created_paths:
            lines.append("Created paths:")
            for path in report.created_paths:
                lines.append(f"  {path}")
    return "\n".join(lines)


def _build_environment(root_dir: Path, data_dir: Path | None = None) -> Environment:
    data = data_dir if data_dir is not None else root_dir / "data"
    return Environment(
        db_path=data / "video_hive.sqlite",
        data_dir=data,
        videos_dir=data / "videos",
        jobs_dir=data / "jobs",
        backup_dir=data / "backups",
        root_dir=root_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Video Hive knowledge videos into Agent Legion workspace jobs."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Run preflight checks only.")
    group.add_argument("--apply", action="store_true", help="Apply the migration.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Path to the data directory (default: ./data).",
    )
    args = parser.parse_args(argv)

    root_dir = Path.cwd()
    env = _build_environment(root_dir, args.data_dir)

    if args.check:
        report = preflight(env)
        print(_render_report(report))
        return 1 if report.blocked else 0

    if args.apply:
        report = apply_migration(env)
        print(_render_report(report))
        return 1 if report.blocked else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
