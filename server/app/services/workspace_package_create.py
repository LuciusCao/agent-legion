"""Workspace package (zip) creation for the job package service.

Extracted from the retired ``server.app.pipeline`` package. The base
artifact list only carries generic result files; business artifacts are
picked up through the workflow catalog's declared node outputs (see
``workspace_package_artifacts.workspace_artifact_names``).
"""

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.storage_paths import ManagedPathError, resolve_job_dir
from server.app.workflows.workflow_manifest import workflow_manifest

WORKSPACE_PACKAGE_FILES = [
    "result.json",
    "metadata.json",
    "report.md",
]


def create_workspace_package(
    jobs: list[Any],
    packages_dir: Path,
    jobs_base_dir: Path,
    artifact_names: list[str] | None = None,
) -> tuple[Path, int]:
    packages_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    package_path = packages_dir / f"workspace-jobs-{stamp}.zip"
    suffix = 1
    while package_path.exists():
        package_path = packages_dir / f"workspace-jobs-{stamp}-{suffix}.zip"
        suffix += 1
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "jobs": [
            {
                "id": job["id"],
                "source_id": job.get("source_id", ""),
                "workflow_key": job.get("workflow_key", ""),
                "workflow": workflow_manifest(job),
                "status": job.get("status", ""),
            }
            for job in jobs
        ],
    }
    names = WORKSPACE_PACKAGE_FILES if artifact_names is None else artifact_names
    job_count = 0
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for job in jobs:
            try:
                job_dir = resolve_job_dir(job, jobs_base_dir)
            except ManagedPathError:
                continue
            if not job_dir.exists():
                continue
            for name in names:
                path = job_dir / name
                if path.exists():
                    zf.write(path, f"{job['id']}/{name}")
            job_count += 1
    return package_path, job_count
