"""Inline stored artifact bodies for quality item/replay details.

Split out of ``quality_labels.py`` for the file-size budget (shared by the
label and replay services).
"""

from __future__ import annotations

from typing import Any

from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore

# Artifact bodies are inlined into the item detail response; cap each blob so
# a pathological output cannot blow up the payload.
_ARTIFACT_CONTENT_LIMIT = 32 * 1024


def artifact_contents(
    artifact_store: ArtifactStore | None,
    job_id: str,
    node_keys: str | set[str],
    object_store: Any = None,
) -> list[dict[str, Any]]:
    """Inline the stored artifact bodies of one job's node(s) (shared by
    quality item details and replay details).

    D12 read order: object-storage manifest first (covers every uploaded
    node, local-pool included), then the legacy content-addressed refs for
    jobs predating the upload path.
    """
    wanted = {node_keys} if isinstance(node_keys, str) else set(node_keys)
    contents: list[dict[str, Any]] = []
    covered: set[str] = set()
    if object_store is not None and object_store.enabled:
        for row in object_store.rows_for_job(job_id):
            if row["node_key"] not in wanted:
                continue
            try:
                # Bounded read: the object can be orders of magnitude larger
                # than the inline cap, so never pull it fully into memory.
                stream = object_store.open_stream(row)
                try:
                    raw = stream.read(_ARTIFACT_CONTENT_LIMIT + 1)
                finally:
                    stream.close()
            except Exception:
                continue
            contents.append(
                {
                    "name": row["name"],
                    "content": raw[:_ARTIFACT_CONTENT_LIMIT].decode("utf-8", errors="replace"),
                    "truncated": len(raw) > _ARTIFACT_CONTENT_LIMIT,
                }
            )
            covered.add(str(row["name"]))
    if artifact_store is None:
        return contents
    for ref in artifact_store.refs_for_job(job_id):
        if ref["node_key"] not in wanted or ref["name"] in covered:
            continue
        try:
            path = artifact_store.open(ref["hash"])
            raw = path.read_bytes()
        except (ArtifactNotFoundError, OSError):
            continue
        contents.append(
            {
                "name": ref["name"],
                "content": raw[:_ARTIFACT_CONTENT_LIMIT].decode("utf-8", errors="replace"),
                "truncated": len(raw) > _ARTIFACT_CONTENT_LIMIT,
            }
        )
    return contents
