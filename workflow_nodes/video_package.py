"""video_knowledge node: write the package manifest for the finished job.

Lists the top-level files of the job directory (``iterdir()``, non-recursive,
excluding the manifest itself) into ``package_manifest.json`` so downstream
packaging/upload sees a stable file inventory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    files = sorted(
        path.name
        for path in job_dir.iterdir()
        if path.is_file() and path.name != "package_manifest.json"
    )
    manifest = {
        "workflow_key": "video_knowledge",
        "job_id": str(job.get("id") or ""),
        "files": files,
    }
    ctx.artifacts.write_json("package_manifest.json", manifest)
