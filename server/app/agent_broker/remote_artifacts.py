"""Apply Worker-direct S3 artifact refs on the result-commit path (#160 D12).

Split out of ``agent_completion.py`` for the file-size budget (mirrors the
``result_unpack.py`` split): the completion handler stays the orchestrator,
this module owns the untrusted-ref verify-then-apply flow (the apply-phase
mechanics live in ``remote_artifact_promote.py``).

Workers upload to a per-execution staging key (``jobs-staging/...``); the
Host verifies EVERY ref first (staging layout bound to this execution, size
ceiling, HEAD size), downloads declared outputs into a staging dir next to
the job dir and hash-checks them (cancelled runs skip the download but still
digest-verify the bytes), and only then applies: server-side copy onto the
authority key, atomic promote into the job dir, manifest rows in ONE
transaction. Staging objects are never deleted inside this window — the
completion tail deletes them only AFTER the finish commits (concurrent
/result retries still read them in between, #774 对抗复审 P1); residue of
every other outcome belongs to the bucket lifecycle / ``s3_jobs_gc``.
Any earlier failure applies nothing — no half-applied outputs.

#338: refs are dual-form — a ``.gz``-suffixed staging key holds gzip bytes
(HEAD verifies the compressed size the Worker reports; downloads decode
transparently, so the job-dir copy and every digest stay uncompressed), a
bare key holds raw bytes (older Workers). The authority key keeps the
staging ref's suffix.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.agent_broker.remote_artifact_promote import promote_all
from server.app.agent_broker.remote_artifact_support import (
    DEFAULT_SPOT_CHECK_PERCENT,
    download_remote_artifact,
    verify_remote_digest,
)
from server.app.executors.models import ExecutionResult
from server.app.services.job_artifact_names import is_downloadable_artifact_name
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
    lease_id: str,
    max_size_bytes: int | None = None,
    spot_check_percent: int | None = None,
) -> tuple[set[str], str | None]:
    """Verify + apply the dict-form (staging-key) refs in output_artifacts.

    Returns ``(remote_names, error)``: names carried as object-storage refs
    (empty for a legacy-only result) and the failure message for the whole
    result, if any. ``download`` is False for cancelled runs — partial outputs
    are promoted/registered but never land in the job dir, mirroring the tar
    path; their staging bytes are still digest-verified, so the registered
    hash always comes from Host-verified content. ``max_size_bytes`` applies
    the instance artifact size ceiling (``agent_workers.max_archive_bytes``).
    ``lease_id`` feeds the EXEC-GENERATION-001 promote write gate (#645
    P2-a): a gate rejection surfaces as the same "lease is no longer active"
    failure the finish CAS would have produced.
    """
    remote = {name: ref for name, ref in output_artifacts.items() if isinstance(ref, dict)}
    if not remote:
        return set(), None
    # Worker-reported names are untrusted (#759 review P2): a name whose
    # segments carry a dot-prefix (e.g. ".rollback/out.json") would collide
    # with the promote primitive's rollback-key namespace inside the staging
    # layout — the backup copy could overwrite the Worker staging object and
    # leave the manifest row pointing at hash-mismatched bytes. Reject with
    # the #631 serve-side whitelist (nested declared names stay welcome).
    if bad := next((n for n in sorted(remote) if not is_downloadable_artifact_name(n)), None):
        return set(remote), f"Agent Worker reported invalid artifact name: {bad!r}"
    if object_store is None or not object_store.enabled:
        return set(remote), (
            "Agent Worker reported object-storage artifacts "
            "but object storage is not configured on this Host"
        )
    if not execution_id:
        return set(remote), "Agent Worker result is missing its execution id"
    percent = DEFAULT_SPOT_CHECK_PERCENT if spot_check_percent is None else spot_check_percent
    try:
        # Phase 1: verify EVERY ref (staging layout bound to this execution,
        # size ceiling, HEAD size) before anything is copied, downloaded, or
        # registered. Non-canonical names (``reports//out.json``) are rejected
        # here too (#759 复审 P2): two aliasing names would share one staging
        # path and one job_dir target while registering two manifest rows.
        for name, ref in remote.items():
            if PurePosixPath(name).as_posix() != name:
                raise ValueError(f"non-canonical artifact name: {name!r}")
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
        # name -> hash to register: verified equal to the streamed bytes; an
        # empty Worker report registers the Host-computed digest (the same
        # semantics as the cancelled path below).
        content_hashes: dict[str, str] = {}
        if download:
            with tempfile.TemporaryDirectory(prefix=".artifact-staging-", dir=job_dir) as stage:
                for name, ref in remote.items():
                    if name in expected:
                        staged[name], content_hashes[name] = download_remote_artifact(
                            object_store, Path(stage), name, ref, max_size_bytes
                        )
                    else:
                        # Undeclared names never land in the job dir; a
                        # Worker-reported hash is trusted outside the #356
                        # spot-check sample, an empty one still streams (the
                        # manifest row needs a Host-computed digest).
                        content_hashes[name] = verify_remote_digest(
                            object_store, name, ref, max_size_bytes, spot_check_percent=percent
                        )
                # Phase 3: all verified — promote copies, files, and rows.
                if not promote_all(
                    object_store,
                    workspace_id,
                    job_id,
                    node_key,
                    job_dir,
                    remote,
                    staged,
                    content_hashes,
                    execution_id,
                    lease_id,
                ):
                    return set(remote), "execution lease is no longer active"
                return set(remote), None
        # Cancelled path: no download; a reported hash is trusted outside
        # the #356 spot-check sample, an empty one still streams to compute
        # the digest (stream, never persisted).
        for name, ref in remote.items():
            content_hashes[name] = verify_remote_digest(
                object_store, name, ref, max_size_bytes, spot_check_percent=percent
            )
        if not promote_all(
            object_store,
            workspace_id,
            job_id,
            node_key,
            job_dir,
            remote,
            staged,
            content_hashes,
            execution_id,
            lease_id,
        ):
            return set(remote), "execution lease is no longer active"
    except Exception as exc:
        # #204 broad-except audit: deliberate whole-batch containment. The
        # guarded block's outcome space is mixed by design — Worker-report
        # validation failures (ValueError from verify_remote / hash checks,
        # on untrusted input), storage-layer errors (botocore surface, not a
        # business-exception family), AND unexpected programming errors all
        # must convert into a failed ExecutionResult instead of propagating:
        # this runs inside the completion handler's finish(), where an
        # escape would kill the lease finish path. No partial state escapes
        # (verify-everything-then-apply + promote_all's rollback), and the
        # message embeds the exception so the failure lands on the node row
        # for the operator.
        return set(remote), f"failed to apply Worker artifact uploads: {exc}"
    return set(remote), None
