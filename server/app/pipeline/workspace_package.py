import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.storage_paths import ManagedPathError, resolve_job_dir

WORKSPACE_PACKAGE_FILES = [
    "result.json",
    "comprehension_info.json",
    "question_context.json",
    "questions.json",
    "upload_params.json",
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
    package_path = (
        packages_dir / f"workspace-jobs-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.zip"
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "jobs": [
            {
                "id": job["id"],
                "source_id": job.get("source_id", ""),
                "workflow_key": job.get("workflow_key", ""),
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
