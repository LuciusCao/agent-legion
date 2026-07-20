"""Artifact-reference staging for remote bundles (phase 4, task 3).

When every declared input can be put into the ``ArtifactStore`` before
submit, the manifest is marked ``bundle_mode: "refs"`` and the bundle skips
the input payloads — workers pull them from ``GET /api/artifacts/{hash}``.
Any staging failure (no store wired, IO error, DB error) falls back to the
full bundle, which old workers keep consuming unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.executors.models import ExecutionContext
    from server.app.services.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)

# Workers POST output bytes here before referencing them in the result metadata.
ARTIFACT_UPLOAD_URL = "/api/artifacts"


def stage_input_artifacts(
    store: ArtifactStore | None, context: ExecutionContext, manifest: dict[str, Any]
) -> tuple[str, ...]:
    """Put declared inputs into the store and mark the manifest refs-mode.

    Returns the input names the bundle should skip. Falls back to the full
    bundle (no manifest keys, empty skip list) on any staging failure.
    """
    if store is None or not context.inputs:
        return ()
    try:
        refs: dict[str, str] = {}
        for rel in context.inputs:
            digest = store.put((context.job_dir / rel).read_bytes())
            store.add_ref(context.job_id, context.node_key, rel, digest)
            refs[rel] = f"sha256:{digest}"
    except Exception:
        logger.warning(
            "artifact staging failed for %s; shipping full bundle",
            context.execution_id,
            exc_info=True,
        )
        return ()
    manifest["input_artifacts"] = refs
    manifest["artifact_upload_url"] = ARTIFACT_UPLOAD_URL
    manifest["bundle_mode"] = "refs"
    return tuple(refs)
