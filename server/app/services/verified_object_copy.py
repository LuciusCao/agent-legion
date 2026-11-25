"""Verified, server-side object copies shared by workspace-sharing flows.

Callers own database intents, authorization, key derivation, and publication.
This module owns the complete object-side operation: conditional source copy,
fresh destination HEAD, ETag-conditioned streaming SHA256/size verification,
and best-effort compensation of every destination attempted by the call.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from typing import BinaryIO, Literal

from botocore.exceptions import ClientError

from server.app.storage import ObjectStorage

_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ObjectCopySpec:
    """One immutable source snapshot and its caller-owned destination.

    ``destination_key`` must be single-use for one owning operation and unique
    within the batch. S3 CopyObject cannot require an absent destination, so
    cross-attempt uniqueness comes from the caller's IDs, not an
    overwrite check here. ``source_etag`` is required and uses the unquoted
    ``ObjectHead.etag`` representation.
    """

    source_key: str
    destination_key: str
    source_etag: str
    expected_size_bytes: int
    expected_sha256: str


@dataclass(frozen=True)
class VerifiedObjectCopy:
    source_key: str
    destination_key: str
    object_etag: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ObjectCopyCleanupFailure:
    storage_key: str
    detail: str


VerifiedObjectCopyFailureCode = Literal[
    "invalid_request",
    "source_missing",
    "source_precondition_failed",
    "copy_failed",
    "head_failed",
    "destination_missing",
    "destination_etag_missing",
    "destination_precondition_failed",
    "destination_size_mismatch",
    "destination_hash_mismatch",
    "verification_failed",
]


@dataclass(frozen=True)
class VerifiedObjectCopyFailure:
    code: VerifiedObjectCopyFailureCode
    copy_index: int
    source_key: str
    destination_key: str
    detail: str
    cleanup_failures: tuple[ObjectCopyCleanupFailure, ...] = ()


@dataclass(frozen=True)
class VerifiedObjectCopyResult:
    """All verified copies, or one failure; successful items are never partial."""

    copies: tuple[VerifiedObjectCopy, ...] = ()
    failure: VerifiedObjectCopyFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def copy_objects_verified(
    storage: ObjectStorage, copies: Iterable[ObjectCopySpec]
) -> VerifiedObjectCopyResult:
    """Copy and verify a batch atomically from the caller's publication view.

    Failure returns a typed result instead of leaking storage-specific error
    shapes. Every attempted destination (including one whose copy acknowledgement
    was lost) is cleaned best-effort; cleanup errors are attached without masking
    the original failure. Remaining objects, if any, are not publishable and must
    remain covered by the caller's intent/GC protocol.
    """
    specs = tuple(copies)
    invalid = _validate_batch(specs)
    if invalid is not None:
        return VerifiedObjectCopyResult(failure=invalid)

    attempted: list[str] = []
    verified: list[VerifiedObjectCopy] = []
    for index, spec in enumerate(specs):
        attempted.append(spec.destination_key)
        stage = "copy"
        failure: VerifiedObjectCopyFailure | None = None
        try:
            storage.copy_object(spec.source_key, spec.destination_key, source_etag=spec.source_etag)
            stage = "head"
            head = storage.head_object(spec.destination_key)
            if head is None:
                failure = _failure(spec, index, "destination_missing", "copied object is missing")
            elif not head.etag:
                failure = _failure(
                    spec,
                    index,
                    "destination_etag_missing",
                    "destination HEAD returned no ETag; conditional GET is impossible",
                )
            elif head.size_bytes != spec.expected_size_bytes:
                failure = _failure(
                    spec,
                    index,
                    "destination_size_mismatch",
                    f"destination HEAD size {head.size_bytes} != expected "
                    f"{spec.expected_size_bytes}",
                )
            else:
                stage = "get"
                with closing(
                    storage.open_stream(spec.destination_key, if_match=head.etag)
                ) as stream:
                    streamed_size, actual_sha256 = _sha256_stream(stream)
                if streamed_size != spec.expected_size_bytes:
                    failure = _failure(
                        spec,
                        index,
                        "destination_size_mismatch",
                        f"destination stream size {streamed_size} != expected "
                        f"{spec.expected_size_bytes}",
                    )
                elif actual_sha256 != spec.expected_sha256:
                    failure = _failure(
                        spec,
                        index,
                        "destination_hash_mismatch",
                        f"destination sha256 {actual_sha256} != expected {spec.expected_sha256}",
                    )
        except BaseException as exc:
            # #204 broad-except audit: compensate every possibly-written key even
            # for process-control exceptions. Ordinary storage failures become an
            # explicit result; KeyboardInterrupt/SystemExit re-raise after cleanup.
            if not isinstance(exc, Exception):
                _cleanup_destinations(storage, attempted)
                raise
            failure = _exception_failure(spec, index, stage, exc)

        if failure is None:
            assert head is not None
            verified.append(
                VerifiedObjectCopy(
                    source_key=spec.source_key,
                    destination_key=spec.destination_key,
                    object_etag=head.etag,
                    size_bytes=spec.expected_size_bytes,
                    sha256=spec.expected_sha256,
                )
            )
            continue
        cleanup_failures = _cleanup_destinations(storage, attempted)
        return VerifiedObjectCopyResult(failure=replace(failure, cleanup_failures=cleanup_failures))

    return VerifiedObjectCopyResult(copies=tuple(verified))


def _validate_batch(specs: tuple[ObjectCopySpec, ...]) -> VerifiedObjectCopyFailure | None:
    destination_keys: set[str] = set()
    for index, spec in enumerate(specs):
        detail = ""
        if not spec.source_key or not spec.destination_key:
            detail = "source_key and destination_key must be non-empty"
        elif spec.source_key == spec.destination_key:
            detail = "source_key and destination_key must differ"
        elif not spec.source_etag:
            detail = "source_etag is required for verified copy"
        elif spec.expected_size_bytes < 0:
            detail = "expected_size_bytes must be non-negative"
        elif not _SHA256_HEX_RE.fullmatch(spec.expected_sha256):
            detail = "expected_sha256 must be a lowercase sha256 hex digest"
        elif spec.destination_key in destination_keys:
            detail = "destination_key must be unique within the batch"
        if detail:
            return _failure(spec, index, "invalid_request", detail)
        destination_keys.add(spec.destination_key)
    return None


def _failure(
    spec: ObjectCopySpec,
    index: int,
    code: VerifiedObjectCopyFailureCode,
    detail: str,
) -> VerifiedObjectCopyFailure:
    return VerifiedObjectCopyFailure(
        code=code,
        copy_index=index,
        source_key=spec.source_key,
        destination_key=spec.destination_key,
        detail=detail,
    )


def _exception_failure(
    spec: ObjectCopySpec, index: int, stage: str, exc: Exception
) -> VerifiedObjectCopyFailure:
    code: VerifiedObjectCopyFailureCode
    if stage == "copy":
        code = "copy_failed"
    elif stage == "head":
        code = "head_failed"
    else:
        code = "verification_failed"
    storage_code = ""
    if isinstance(exc, ClientError):
        storage_code = str(exc.response.get("Error", {}).get("Code", ""))
        if storage_code in ("404", "NoSuchKey", "NotFound"):
            code = "source_missing" if stage == "copy" else "destination_missing"
        elif storage_code in ("412", "PreconditionFailed"):
            code = (
                "source_precondition_failed"
                if stage == "copy"
                else "destination_precondition_failed"
            )
    suffix = f" (S3 {storage_code})" if storage_code else ""
    return _failure(
        spec,
        index,
        code,
        f"{stage} failed with {type(exc).__name__}: {exc}{suffix}",
    )


def _sha256_stream(stream: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := stream.read(_HASH_CHUNK_BYTES):
        size_bytes += len(chunk)
        digest.update(chunk)
    return size_bytes, digest.hexdigest()


def _cleanup_destinations(
    storage: ObjectStorage, destination_keys: Iterable[str]
) -> tuple[ObjectCopyCleanupFailure, ...]:
    failures: list[ObjectCopyCleanupFailure] = []
    for key in reversed(tuple(destination_keys)):
        try:
            storage.delete_object(key)
        except Exception as exc:
            # #204 broad-except audit: cleanup is best-effort for every key. The
            # typed result retains each failure and the original copy error; no
            # exception is swallowed without being returned to the caller.
            failures.append(ObjectCopyCleanupFailure(key, f"{type(exc).__name__}: {exc}"))
    return tuple(failures)
