"""video_knowledge node: write the package manifest for the finished job.

Lists every file in the job directory (excluding the manifest itself) into
``package_manifest.json`` so downstream packaging/upload sees a stable file
inventory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
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
    (job_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
