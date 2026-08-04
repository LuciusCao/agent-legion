from __future__ import annotations

from typing import Any

from server.app.executors.models import ExecutionContext
from server.app.services.artifact_store import ArtifactStore


def stage_agent_inputs(
    store: ArtifactStore, context: ExecutionContext, manifest: dict[str, Any]
) -> None:
    manifest["bundle_mode"] = "refs"
    manifest["artifact_upload_url"] = "/api/artifacts"
    refs: dict[str, str] = {}
    for relative_path in context.inputs:
        digest = store.put((context.job_dir / relative_path).read_bytes())
        store.add_ref(context.job_id, context.node_key, relative_path, digest)
        refs[relative_path] = f"sha256:{digest}"
    manifest["input_artifacts"] = refs
