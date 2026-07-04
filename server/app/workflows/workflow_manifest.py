from __future__ import annotations

from typing import Any


def workflow_manifest(job: dict[str, Any], default_key: str = "") -> dict[str, Any]:
    return {
        "key": job.get("workflow_key", default_key),
        "version": job.get("workflow_version"),
        "revision_id": job.get("workflow_revision_id", ""),
        "definition_hash": job.get("workflow_definition_hash", ""),
    }
