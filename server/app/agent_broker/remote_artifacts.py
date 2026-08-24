"""Apply Worker-direct S3 artifact refs on the result-commit path (#160 D12).

Split out of ``agent_completion.py`` for the file-size budget (mirrors the
``result_unpack.py`` split): the completion handler stays the orchestrator,
this module owns the untrusted-ref mechanics — verify EVERY ref first, then
download + register (no half-applied state).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.services.job_artifact_objects import JobArtifactObjectStore


def apply_remote_artifact_refs(
    object_store: JobArtifactObjectStore | None,
    *,
    workspace_id: str,
    job_id: str,
    node_key: str,
    job_dir: Path,
    expected: tuple[str, ...],
    output_artifacts: dict[str, Any],
    download: bool,
) -> tuple[set[str], str | None]:
    """Verify + apply the dict-form (object-storage) refs in output_artifacts.

    Returns ``(remote_names, error)``: the names carried as object-storage
    refs (empty for a legacy-only result) and the failure message for the
    whole result, if any. Every ref is HEAD-verified BEFORE anything is
    downloaded or registered. Downloads land only for declared
    ``expected_outputs`` names (the same whitelist as the tar unpack path)
    via temp file + atomic rename; undeclared names are registered but never
    promoted. ``download`` is False for cancelled runs — partial outputs are
    registered but not promoted, mirroring the tar path.
    """
    remote = {name: ref for name, ref in output_artifacts.items() if isinstance(ref, dict)}
    if not remote:
        return set(), None
    if object_store is None or not object_store.enabled:
        return set(remote), (
            "Agent Worker reported object-storage artifacts "
            "but object storage is not configured on this Host"
        )
    try:
        for name, ref in remote.items():
            object_store.verify_remote(
                workspace_id=workspace_id,
                job_id=job_id,
                name=name,
                storage_key=str(ref["storage_key"]),
                size_bytes=int(ref["size_bytes"]),
            )
        if download:
            for name, ref in remote.items():
                if name in expected:
                    _download_remote_artifact(object_store, job_dir, name, ref)
        for name, ref in remote.items():
            object_store.record_remote(
                workspace_id=workspace_id,
                job_id=job_id,
                node_key=node_key,
                name=name,
                storage_key=str(ref["storage_key"]),
                size_bytes=int(ref["size_bytes"]),
                content_hash=str(ref.get("content_hash") or ""),
            )
    except Exception as exc:
        return set(remote), f"failed to apply Worker artifact uploads: {exc}"
    return set(remote), None


def _download_remote_artifact(
    store: JobArtifactObjectStore, job_dir: Path, name: str, ref: Any
) -> None:
    """Stream one verified S3 artifact into the job dir (atomic)."""
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe expected output name: {name!r}")
    target = job_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
    temporary_path = Path(temporary)
    try:
        digest = hashlib.sha256()
        with (
            os.fdopen(fd, "wb") as handle,
            store.open_stream({"storage_key": str(ref["storage_key"])}) as stream,
        ):
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
                handle.write(chunk)
        declared = str(ref.get("content_hash") or "")
        if declared and digest.hexdigest() != declared:
            raise ValueError(f"artifact content hash mismatch: {name!r}")
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
