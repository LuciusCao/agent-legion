from __future__ import annotations

from typing import Any


def workflow_manifest(job: dict[str, Any], default_key: str = "") -> dict[str, Any]:
    # #211 M2: jobs lost the workflow_key column (v70); the exported
    # manifest keeps the identity value (workflow_key == workspace_id).
    return {
        "key": job.get("workflow_key") or job.get("workspace_id", default_key),
        "version": job.get("workflow_version"),
        "revision_id": job.get("workflow_revision_id", ""),
        "definition_hash": job.get("workflow_definition_hash", ""),
    }
