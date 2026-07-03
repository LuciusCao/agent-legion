"""Shared helpers and data structures for the Video Hive migration CLI."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.workflows.registry import load_registered_workflow

VIDEO_NODES = (
    "download",
    "transcribe",
    "subtitle_review",
    "chapter_generate",
    "interaction_generate",
    "content_review",
    "assemble",
    "package",
)

LEGACY_PHASES = frozenset(
    {
        "waiting_for_url",
        "download",
        "transcribe",
        "subtitle_review",
        "chapter_generate",
        "interaction_generate",
        "content_review",
        "assemble",
    }
)

PHASE_TO_NODE = {
    "waiting_for_url": "download",
    "download": "download",
    "transcribe": "transcribe",
    "subtitle_review": "subtitle_review",
    "chapter_generate": "chapter_generate",
    "interaction_generate": "interaction_generate",
    "content_review": "content_review",
    "assemble": "assemble",
}

WORKSPACE_ID = "video_knowledge"
WORKFLOW_KEY = "video_knowledge"

COMPLETED_REQUIRED_ARTIFACTS = (
    "subtitles.srt",
    "transcription.json",
    "subtitles_reviewed.srt",
    "subtitle_review_report.json",
    "chapters_raw.json",
    "chapters.json",
    "interactions.json",
    "checklist.json",
    "review_result.json",
    "metadata.json",
    "report.md",
    "upload_params.json",
)


@dataclass(frozen=True)
class MigrationError:
    legacy_video_id: str
    message: str


@dataclass(frozen=True)
class VideoMapping:
    legacy_video_id: str
    job_id: str
    source_id: str
    title: str


@dataclass
class MigrationReport:
    blocked: bool
    mappings: list[VideoMapping]
    errors: list[MigrationError]
    created_paths: list[str]


@dataclass(frozen=True)
class Environment:
    db_path: Path
    data_dir: Path
    videos_dir: Path
    jobs_dir: Path
    backup_dir: Path
    root_dir: Path


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def job_id_for_video(video: Mapping[str, Any]) -> str:
    source = str(video.get("external_id") or video["id"]).strip()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", source).strip("-") or str(video["id"])
    return f"video-{slug}"


def _source_id_for_video(video: Mapping[str, Any]) -> str:
    return str(video.get("external_id") or video["id"]).strip()


def _video_input_for_video(video: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entity_type": "video",
        "content_type": "knowledge",
        "legacy_video_id": str(video["id"]),
        "external_id": str(video.get("external_id") or "").strip(),
        "source_uuid": str(video.get("source_uuid") or "").strip(),
        "source_url": str(video.get("source_url") or "").strip(),
        "title": str(video.get("title") or "").strip(),
    }


def _list_legacy_videos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("select * from videos order by created_at, id")]


def _list_phase_runs(conn: sqlite3.Connection, video_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "select * from phase_runs where video_id=? order by id", (video_id,)
        )
    ]


def _list_transcription_runs(conn: sqlite3.Connection, video_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "select * from transcription_runs where video_id=? order by id", (video_id,)
        )
    ]


def _job_status_from_nodes(node_statuses: Mapping[str, str]) -> str:
    values = list(node_statuses.values())
    if any(status == "failed" for status in values):
        return "failed"
    if all(status == "completed" for status in values):
        return "completed"
    return "queued"


def _map_node_statuses(status: str, current_phase: str) -> dict[str, str]:
    node_statuses = {node: "pending" for node in VIDEO_NODES}

    if status == "completed":
        for node in VIDEO_NODES:
            node_statuses[node] = "completed"
        return node_statuses

    phase_node: str | None = PHASE_TO_NODE.get(current_phase)
    if phase_node is None:
        return node_statuses

    if status == "failed" or status == "missing_url":
        for n in VIDEO_NODES:
            if n == phase_node:
                node_statuses[n] = "failed"
                break
            node_statuses[n] = "completed"
    elif status == "queued":
        for n in VIDEO_NODES:
            if n == phase_node:
                node_statuses[n] = "pending"
                break
            node_statuses[n] = "completed"

    return node_statuses


def _ensure_workspace(conn: sqlite3.Connection) -> None:
    existing = conn.execute("select 1 from workspaces where id=?", (WORKSPACE_ID,)).fetchone()
    if existing is not None:
        return
    conn.execute(
        "insert into workspaces(id, name, description, default_workflow_key, default_entity) "
        "values (?, ?, ?, ?, ?)",
        (
            WORKSPACE_ID,
            "Video Knowledge",
            "Migrated Video Hive knowledge videos",
            WORKFLOW_KEY,
            "video",
        ),
    )


def _workflow_definition(env: Environment) -> Any:
    return load_registered_workflow(env.root_dir, WORKFLOW_KEY)


def _legacy_source_mp4(video: Mapping[str, Any], video_dir: Path) -> Path | None:
    source_path = video_dir / "source.mp4"
    if source_path.is_file():
        return source_path
    legacy_path = video_dir / f"{video['id']}.mp4"
    if legacy_path.is_file():
        return legacy_path
    return None


def _ensure_canonical_source_mp4(video: Mapping[str, Any], job_dir: Path) -> None:
    if (job_dir / "source.mp4").is_file():
        return
    legacy_path = job_dir / f"{video['id']}.mp4"
    if legacy_path.is_file():
        shutil.move(legacy_path, job_dir / "source.mp4")


def _missing_completed_artifacts(video: Mapping[str, Any], video_dir: Path) -> list[str]:
    missing = [name for name in COMPLETED_REQUIRED_ARTIFACTS if not (video_dir / name).is_file()]
    if _legacy_source_mp4(video, video_dir) is None:
        missing.insert(0, "source.mp4")
    return missing


def _write_package_manifest(job_dir: Path) -> None:
    files = sorted(
        path.name
        for path in job_dir.iterdir()
        if path.is_file() and path.name != "package_manifest.json"
    )
    manifest = {
        "workflow_key": WORKFLOW_KEY,
        "job_id": job_dir.name,
        "files": files,
    }
    (job_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_created_dirs(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        if path.exists():
            shutil.rmtree(path)
