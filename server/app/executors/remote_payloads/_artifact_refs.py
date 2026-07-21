"""Artifact-reference staging for refs-only remote bundles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.executors.models import ExecutionContext
    from server.app.services.artifact_store import ArtifactStore

# Workers POST output bytes here before referencing them in the result metadata.
ARTIFACT_UPLOAD_URL = "/api/artifacts"


def stage_input_artifacts(
    store: ArtifactStore | None, context: ExecutionContext, manifest: dict[str, Any]
) -> None:
    """Stage all inputs as artifact refs, raising on any configuration/IO failure."""
    manifest["bundle_mode"] = "refs"
    manifest["artifact_upload_url"] = ARTIFACT_UPLOAD_URL
    if not context.inputs:
        return
    if store is None:
        raise RuntimeError(
            f"artifact store is required for remote submission of {context.execution_id}"
        )
    refs: dict[str, str] = {}
    for rel in context.inputs:
        digest = store.put((context.job_dir / rel).read_bytes())
        store.add_ref(context.job_id, context.node_key, rel, digest)
        refs[rel] = f"sha256:{digest}"
    manifest["input_artifacts"] = refs
