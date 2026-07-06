from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.storage_paths import ManagedPathError, resolve_data_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillVersionBackfillResult:
    node_runs_updated: int
    manifests_updated: int


def backfill_node_run_skill_versions(
    conn: sqlite3.Connection,
    data_dir: Path,
) -> SkillVersionBackfillResult:
    """Backfill empty node_runs.skill_version values from run.json files."""
    rows = conn.execute(
        """
        select id, job_id, node_key, run_dir
        from node_runs
        where skill_version = '' and run_dir != '' and status in ('completed', 'failed')
        """
    ).fetchall()

    job_ids: set[str] = _jobs_with_persisted_versions(conn)
    updated = 0
    for row in rows:
        version = _read_run_skill_version(data_dir, str(row["run_dir"]), int(row["id"]))
        if not version:
            continue
        conn.execute(
            "update node_runs set skill_version = ? where id = ?",
            (version, row["id"]),
        )
        job_ids.add(str(row["job_id"]))
        updated += 1

    manifests_updated = _refresh_manifests(conn, data_dir, job_ids)
    return SkillVersionBackfillResult(updated, manifests_updated)


def _read_run_skill_version(data_dir: Path, run_dir: str, run_id: int) -> str:
    try:
        run_path = resolve_data_path(run_dir, data_dir, allow_missing=False)
    except (ManagedPathError, FileNotFoundError) as exc:
        logger.debug("Cannot resolve run_dir for node_run %s: %s", run_id, exc)
        return ""

    run_json = run_path / "run.json"
    if not run_json.is_file():
        return ""
    try:
        payload = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read run.json for node_run %s: %s", run_id, exc)
        return ""
    version = payload.get("skill_version") if isinstance(payload, dict) else ""
    return version if isinstance(version, str) and version else ""


def _refresh_manifests(
    conn: sqlite3.Connection,
    data_dir: Path,
    job_ids: set[str],
) -> int:
    updated = 0
    for job_id in job_ids:
        versions = _job_versions(conn, job_id)
        if not versions:
            continue
        job = conn.execute("select id, storage_dir from jobs where id=?", (job_id,)).fetchone()
        if job is None:
            continue
        manifest_path = _manifest_path(data_dir, dict(job))
        if manifest_path is None or not manifest_path.is_file():
            continue
        if _update_manifest(manifest_path, versions):
            updated += 1
    return updated


def _job_versions(conn: sqlite3.Connection, job_id: str) -> dict[str, str]:
    rows = conn.execute(
        """
        select node_key, skill_version
        from node_runs
        where job_id=? and skill_version != ''
        order by id
        """,
        (job_id,),
    ).fetchall()
    versions: dict[str, str] = {}
    for row in rows:
        versions[str(row["node_key"])] = str(row["skill_version"])
    return versions


def _jobs_with_persisted_versions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "select distinct job_id from node_runs where skill_version != ''"
    ).fetchall()
    return {str(row["job_id"]) for row in rows}


def _manifest_path(data_dir: Path, job: dict[str, Any]) -> Path | None:
    storage_dir = str(job.get("storage_dir", ""))
    if not storage_dir:
        return None
    try:
        job_dir = resolve_data_path(storage_dir, data_dir, allow_missing=False)
    except (ManagedPathError, FileNotFoundError) as exc:
        logger.warning("Cannot resolve storage_dir for job %s: %s", job.get("id", ""), exc)
        return None
    return job_dir / "manifest.json"


def _update_manifest(manifest_path: Path, versions: dict[str, str]) -> bool:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read manifest %s: %s", manifest_path, exc)
        return False
    if not isinstance(payload, dict):
        return False
    existing = payload.get("skill_versions")
    if not isinstance(existing, dict):
        return False

    if existing == versions:
        return False
    payload["skill_versions"] = versions
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
