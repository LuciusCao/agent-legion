from __future__ import annotations

from typing import Any


def is_workflow_outdated(job: dict[str, Any], active: dict[str, Any] | None) -> bool:
    if active is None:
        return False
    revision_id = str(job.get("workflow_revision_id") or "")
    if revision_id:
        return revision_id != str(active["id"])
    version = job.get("workflow_version")
    return version is not None and int(version) != int(active["version"])
