"""Apply Worker-direct S3 artifact refs on the result-commit path (#160 D12).

Split out of ``agent_completion.py`` for the file-size budget (mirrors the
``result_unpack.py`` split): the completion handler stays the orchestrator,
this module owns the untrusted-ref mechanics.

Workers upload to a per-execution staging key (``jobs-staging/...``); the
Host verifies EVERY ref first (staging layout bound to this execution, size
ceiling, HEAD size), downloads declared outputs into a staging dir next to
the job dir and hash-checks them (cancelled runs skip the download but still
digest-verify the bytes), and only then applies: server-side copy onto the
authority key, atomic promote into the job dir, manifest rows in ONE
transaction, staging cleanup. Any earlier failure applies nothing — no
half-applied outputs.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from server.app.agent_broker.remote_artifact_support import (
    build_manifest_rows,
    discard_staging,
    download_remote_artifact,
    promote_remote,
    verify_remote_digest,
)
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
    max_size_bytes: int | None = None,
) -> tuple[set[str], str | None]:
    """Verify + apply the dict-form (staging-key) refs in output_artifacts.

    Returns ``(remote_names, error)``: names carried as object-storage refs
    (empty for a legacy-only result) and the failure message for the whole
    result, if any. ``download`` is False for cancelled runs — partial outputs
    are promoted/registered but never land in the job dir, mirroring the tar
    path; their staging bytes are still digest-verified, so the registered
    hash always comes from Host-verified content. ``max_size_bytes`` applies
    the instance artifact size ceiling (``agent_workers.max_archive_bytes``).
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
        # size ceiling, HEAD size) before anything is copied, downloaded, or
        # registered.
        for name, ref in remote.items():
            object_store.verify_remote(
                workspace_id=workspace_id,
                job_id=job_id,
                name=name,
                storage_key=str(ref["storage_key"]),
                size_bytes=int(ref["size_bytes"]),
                execution_id=execution_id,
                max_size_bytes=max_size_bytes,
            )
        # Phase 2: download every declared output into a same-filesystem
        # staging dir and hash-check it; the temp dir self-cleans on failure.
        staged: dict[str, Path] = {}
        # name -> hash to register: verified equal to the streamed bytes.
        content_hashes: dict[str, str] = {}
        if download:
            with tempfile.TemporaryDirectory(prefix=".artifact-staging-", dir=job_dir) as stage:
                for name, ref in remote.items():
                    if name in expected:
                        staged[name] = download_remote_artifact(
                            object_store, Path(stage), name, ref
                        )
                    content_hashes[name] = str(ref.get("content_hash") or "")
                # Phase 3: all verified — promote copies, files, and rows.
                _promote_all(
                    object_store,
                    workspace_id,
                    job_id,
                    node_key,
                    job_dir,
                    remote,
                    staged,
                    content_hashes,
                )
                return set(remote), None
        # Cancelled path: no download, but the staging bytes are still
        # digest-verified Host-side (stream, never persisted) — a reported
        # hash must match and an empty one registers the computed value.
        for name, ref in remote.items():
            content_hashes[name] = verify_remote_digest(object_store, name, ref)
        _promote_all(
            object_store,
            workspace_id,
            job_id,
            node_key,
            job_dir,
            remote,
            staged,
            content_hashes,
        )
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
    content_hashes: dict[str, str],
) -> None:
    """Copy to authority keys, promote staged files, register rows, clean up.

    Undeclared names are promoted/registered but never land in the job dir
    (the same whitelist as the tar unpack path). Copies precede row writes:
    a failure between them leaves orphaned authority objects (lifecycle
    backstop), never dangling manifest rows. All manifest rows upsert in ONE
    transaction (record_remote_many): a mid-batch failure rolls back instead
    of leaving a half-registered manifest.
    """
    authority_keys: dict[str, str] = {}
    for name, ref in remote.items():
        authority_keys[name] = promote_remote(
            object_store,
            workspace_id=workspace_id,
            job_id=job_id,
            name=name,
            storage_key=str(ref["storage_key"]),
        )
    for name, staged_path in staged.items():
        target = job_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, target)
    object_store.record_remote_many(
        build_manifest_rows(workspace_id, job_id, node_key, remote, authority_keys, content_hashes)
    )
    for ref in remote.values():
        discard_staging(object_store, str(ref["storage_key"]))
