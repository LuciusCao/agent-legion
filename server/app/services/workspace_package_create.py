"""Workspace package (zip) creation for the job package service.

Extracted from the retired ``server.app.pipeline`` package. The base
artifact list only carries generic result files; business artifacts are
picked up through the workflow catalog's declared node outputs (see
``workspace_package_artifacts.workspace_artifact_names``).
"""

import json
import logging
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.app.storage_paths import ManagedPathError, resolve_job_dir
from server.app.workflows.workflow_manifest import workflow_manifest

logger = logging.getLogger(__name__)

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
    object_store: Any = None,
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
            # D12: the local job_dir is an evictable cache — missing entries
            # fall back to the object-storage manifest. A job only counts
            # when its dir exists (legacy semantics) or an entry was written.
            wrote = False
            for name in names:
                path = job_dir / name
                if job_dir.exists() and path.exists():
                    try:
                        zf.write(path, f"{job['id']}/{name}")
                    except OSError:
                        # 单个产物读取失败（如并发淘汰）不拖垮整个打包。
                        logger.warning(
                            "skipping unreadable artifact %s for job %s",
                            name,
                            job["id"],
                            exc_info=True,
                        )
                        continue
                    wrote = True
                elif object_store is not None and object_store.enabled:
                    try:
                        row = object_store.lookup(str(job["id"]), name)
                        if row is not None:
                            # 流式拷贝：大对象不得整个读进内存。with 项按序进入，
                            # 先开源流再建 zip 条目，对象缺失时不留半截/空条目。
                            with (
                                object_store.open_stream(row) as src,
                                zf.open(f"{job['id']}/{name}", mode="w", force_zip64=True) as dest,
                            ):
                                shutil.copyfileobj(src, dest, 1 << 20)
                            wrote = True
                    except Exception:
                        # 对象缺失/存储故障时跳过该条目，打包继续。
                        logger.warning(
                            "skipping stored artifact %s for job %s",
                            name,
                            job["id"],
                            exc_info=True,
                        )
                        continue
            if job_dir.exists() or wrote:
                job_count += 1
    return package_path, job_count
