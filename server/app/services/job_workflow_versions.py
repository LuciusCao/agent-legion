from __future__ import annotations

from typing import Any


def is_workflow_outdated(job: dict[str, Any], active: dict[str, Any] | None) -> bool:
    if active is None:
        return False
    revision_id = str(job.get("workflow_revision_id") or "")
    if revision_id:
        if revision_id != str(active["id"]):
            return True
        # Same revision id with a stale definition snapshot (older upgrade
        # paths moved the pin without swapping the snapshot) is effectively
        # outdated: dispatch resolves from the snapshot. List reads exclude
        # the multi-KB snapshot itself, so the hash column is the available
        # proxy here — upgrade re-validates against the actual snapshot
        # content before skipping.
        return str(job.get("workflow_definition_hash") or "") != str(active["definition_hash"])
    version = job.get("workflow_version")
    if version is None:
        return True
    return int(version) != int(active["version"])
