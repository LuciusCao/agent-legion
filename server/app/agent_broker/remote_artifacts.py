"""Apply Worker-direct S3 artifact refs on the result-commit path (#160 D12).

Split out of ``agent_completion.py`` for the file-size budget (mirrors the
``result_unpack.py`` split): the completion handler stays the orchestrator,
this module owns the untrusted-ref mechanics.

Workers upload to a per-execution staging key (``jobs-staging/...``); the
Host verifies EVERY ref first (staging key layout bound to this execution,
HEAD size), downloads every declared output into a staging directory next to
the job dir and hash-checks it, and only then applies: server-side copy onto
the authority key, atomic promote into the job dir, manifest rows, staging
cleanup. Any earlier failure applies nothing — no half-applied outputs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.executors.models import ExecutionResult
from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)


def apply_worker_artifact_refs(
    object_store: JobArtifactObjectStore | None,
    *,
    runner: str,
    **kwargs: Any,
) -> tuple[set[str], ExecutionResult | None]:
    """``apply_remote_artifact_refs`` + failure ExecutionResult (one call site).

    Keeps the completion handler's finish() a single orchestration line per
    concern; the mechanics stay in ``apply_remote_artifact_refs`` below.
    """
    names, error = apply_remote_artifact_refs(object_store, **kwargs)
    if error is None:
        return names, None
    return names, ExecutionResult(status="failed", exit_code=1, error_message=error, runner=runner)


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
    execution_id: str,
) -> tuple[set[str], str | None]:
    """Verify + apply the dict-form (staging-key) refs in output_artifacts.

    Returns ``(remote_names, error)``: the names carried as object-storage
    refs (empty for a legacy-only result) and the failure message for the
    whole result, if any. ``download`` is False for cancelled runs — partial
    outputs are promoted/registered but never land in the job dir, mirroring
    the tar path.
    """
    remote = {name: ref for name, ref in output_artifacts.items() if isinstance(ref, dict)}
    if not remote:
        return set(), None
    if object_store is None or not object_store.enabled:
        return set(remote), (
            "Agent Worker reported object-storage artifacts "
            "but object storage is not configured on this Host"
        )
    if not execution_id:
        return set(remote), "Agent Worker result is missing its execution id"
    try:
        # Phase 1: verify EVERY ref (staging layout bound to this execution,
        # HEAD size) before anything is copied, downloaded, or registered.
        for name, ref in remote.items():
            object_store.verify_remote(
                workspace_id=workspace_id,
                job_id=job_id,
                name=name,
                storage_key=str(ref["storage_key"]),
                size_bytes=int(ref["size_bytes"]),
                execution_id=execution_id,
            )
        # Phase 2: download every declared output into a same-filesystem
        # staging dir and hash-check it; the temp dir self-cleans on failure.
        staged: dict[str, Path] = {}
        if download:
            with tempfile.TemporaryDirectory(prefix=".artifact-staging-", dir=job_dir) as stage:
                for name, ref in remote.items():
                    if name in expected:
                        staged[name] = _download_remote_artifact(
                            object_store, Path(stage), name, ref
                        )
                # Phase 3: all verified — promote copies, files, and rows.
                _promote_all(object_store, workspace_id, job_id, node_key, job_dir, remote, staged)
                return set(remote), None
        _promote_all(object_store, workspace_id, job_id, node_key, job_dir, remote, staged)
    except Exception as exc:
        return set(remote), f"failed to apply Worker artifact uploads: {exc}"
    return set(remote), None


def _promote_all(
    object_store: JobArtifactObjectStore,
    workspace_id: str,
    job_id: str,
    node_key: str,
    job_dir: Path,
    remote: dict[str, Any],
    staged: dict[str, Path],
) -> None:
    """Copy to authority keys, promote staged files, register rows, clean up.

    Undeclared names are promoted/registered but never land in the job dir
    (the same whitelist as the tar unpack path). Copies precede row writes:
    a failure between them leaves orphaned authority objects (lifecycle
    backstop), never dangling manifest rows.
    """
    authority_keys: dict[str, str] = {}
    for name, ref in remote.items():
        authority_keys[name] = object_store.promote_remote(
            workspace_id=workspace_id,
            job_id=job_id,
            name=name,
            storage_key=str(ref["storage_key"]),
        )
    for name, staged_path in staged.items():
        target = job_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, target)
    for name, ref in remote.items():
        object_store.record_remote(
            workspace_id=workspace_id,
            job_id=job_id,
            node_key=node_key,
            name=name,
            storage_key=authority_keys[name],
            size_bytes=int(ref["size_bytes"]),
            content_hash=str(ref.get("content_hash") or ""),
        )
    for ref in remote.values():
        object_store.discard_staging(str(ref["storage_key"]))


def _download_remote_artifact(
    store: JobArtifactObjectStore, staging_dir: Path, name: str, ref: Any
) -> Path:
    """Stream one verified staging object into the staging dir (hash-checked)."""
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe expected output name: {name!r}")
    target = staging_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with (
        store.open_stream({"storage_key": str(ref["storage_key"])}) as stream,
        target.open("wb") as handle,
    ):
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            handle.write(chunk)
    declared = str(ref.get("content_hash") or "")
    if declared and digest.hexdigest() != declared:
        raise ValueError(f"artifact content hash mismatch: {name!r}")
    return target
