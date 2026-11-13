"""Fail-closed, read-only object evidence for dangling job artifacts (#1019).

Only the latest manifest row for each name is probed. ``object_missing``
requires two HEAD misses plus a complete ``jobs/`` LIST that does not contain
the key. Hash checks stream raw or gzip-decoded content with cumulative byte
and deadline budgets (#1507). Every storage, timeout, budget, or decoding
failure is ``unknown`` and therefore not
an actionable recovery candidate.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from server.app.executors.artifact_restore import HASH_MISMATCH
from server.app.services.job_rerun.recovery_hash import OK, UNKNOWN, ArtifactHashLimits, stream_hash
from server.app.services.job_rerun.recovery_hash import (
    error_evidence as _error_evidence,
)
from server.app.storage.s3_client import ObjectHead, S3StorageClient
from server.app.storage.s3_settings import S3Settings
from server.app.workflow_worker.hydration_dangling import OBJECT_MISSING

SIZE_MISMATCH = "size_mismatch"
DANGLING_CANDIDATE_OUTCOMES = frozenset({OBJECT_MISSING, HASH_MISMATCH})
LIST_PREFIX = "jobs/"
LIST_PAGE_SIZE = 1000


def s3_configuration_fingerprint(settings: S3Settings) -> dict[str, str]:
    """Hash only non-secret connection identity; credentials never enter the plan."""
    values = {
        "bucket": settings.bucket,
        "endpoint": settings.endpoint_url,
        "public_endpoint": settings.public_endpoint_url,
        "region": settings.region,
    }
    fingerprint = {
        key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in values.items()
    }
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "algorithm": "sha256",
        **fingerprint,
        "configuration": hashlib.sha256(canonical).hexdigest(),
    }


class ArtifactDanglingStorage(Protocol):
    def head_bucket(self) -> None: ...

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]: ...

    def head_object(self, storage_key: str) -> ObjectHead | None: ...

    def open_stream(self, storage_key: str) -> BinaryIO: ...


class S3ArtifactDanglingStorage(S3StorageClient):
    """Read-only S3 operations kept out of the general client's size budget."""

    def head_bucket(self) -> None:
        self._client.head_bucket(Bucket=self._settings.bucket)

    def iter_object_pages(self, prefix: str, *, page_size: int = 1000) -> Iterator[list[str]]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self._settings.bucket,
            Prefix=prefix,
            PaginationConfig={"PageSize": page_size},
        ):
            yield [str(item["Key"]) for item in page.get("Contents") or []]


@dataclass(frozen=True)
class StorageProbe:
    evidence: dict[str, Any]
    listed_references: frozenset[str]


def _head_bucket(storage: ArtifactDanglingStorage) -> dict[str, Any]:
    try:
        storage.head_bucket()
    except Exception as exc:
        # #204 broad-except audit: HEAD Bucket deliberately collapses the mixed
        # botocore/network surface into unknown. No object or DB mutation is
        # attempted, and the sanitized exception identity remains in the plan.
        return {"status": UNKNOWN, "error": _error_evidence(exc)}
    return {"status": OK}


def _list_references(
    storage: ArtifactDanglingStorage, referenced_keys: frozenset[str]
) -> StorageProbe:
    page_count = 0
    key_count = 0
    listed: set[str] = set()
    digest = hashlib.sha256()
    error: dict[str, Any] | None = None
    try:
        for page in storage.iter_object_pages(LIST_PREFIX, page_size=LIST_PAGE_SIZE):
            page_count += 1
            for key in page:
                key_count += 1
                digest.update(json.dumps(key, ensure_ascii=False).encode("utf-8"))
                digest.update(b"\n")
                if key in referenced_keys:
                    listed.add(key)
    except Exception as exc:
        # #204 broad-except audit: pagination may fail at initial LIST or any
        # continuation token. Partial pages never prove absence; the complete
        # flag stays false while preserving sanitized failure evidence.
        error = _error_evidence(exc)
    complete = error is None
    evidence: dict[str, Any] = {
        "prefix": LIST_PREFIX,
        "page_size": LIST_PAGE_SIZE,
        "pagination": "list_objects_v2_continuation",
        "complete": complete,
        "page_count": page_count,
        "key_count": key_count,
        "empty": key_count == 0 if complete else None,
        "referenced_key_count": len(referenced_keys),
        "listed_referenced_key_count": len(listed),
        "keys_sha256": digest.hexdigest(),
    }
    if error is not None:
        evidence["error"] = error
    return StorageProbe(evidence, frozenset(listed))


def probe_storage(
    storage: ArtifactDanglingStorage,
    referenced_keys: Iterable[str],
    *,
    configuration: Mapping[str, str] | None = None,
) -> StorageProbe:
    """Prove bucket reachability and exhaust ``jobs/`` before judging absence."""
    references = frozenset(str(key) for key in referenced_keys)
    listing = _list_references(storage, references)
    if configuration is None:
        fingerprint = getattr(storage, "configuration_fingerprint", None)
        configuration = fingerprint() if callable(fingerprint) else {}
    evidence = {
        "configuration": dict(configuration),
        "head_bucket": _head_bucket(storage),
        "listing": listing.evidence,
    }
    return StorageProbe(evidence, listing.listed_references)


def _head_attempts(
    storage: ArtifactDanglingStorage, key: str
) -> tuple[list[dict], ObjectHead | None]:
    attempts: list[dict[str, Any]] = []
    for _ in range(2):
        try:
            head = storage.head_object(key)
        except Exception as exc:
            # #204 broad-except audit: 403/5xx/timeout and every other HEAD
            # failure are unknown, never absence. The second HEAD is skipped
            # because the first one already failed to provide negative proof.
            attempts.append({"status": UNKNOWN, "error": _error_evidence(exc)})
            return attempts, None
        if head is not None:
            attempts.append({"status": "found", "size_bytes": head.size_bytes})
            return attempts, head
        attempts.append({"status": "not_found", "http_status": 404})
    return attempts, None


def probe_artifact(
    storage: ArtifactDanglingStorage,
    row: Mapping[str, Any],
    storage_probe: StorageProbe,
    *,
    limits: ArtifactHashLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Return structured evidence for one latest row without writing anything."""
    key = str(row["storage_key"])
    attempts, head = _head_attempts(storage, key)
    listing = storage_probe.evidence["listing"]
    listed = key in storage_probe.listed_references if listing["complete"] else None
    list_evidence = {
        "complete": listing["complete"],
        "key_present": listed,
        "within_prefix": key.startswith(LIST_PREFIX),
    }
    result: dict[str, Any] = {
        "storage_key": key,
        "head": {"attempts": attempts},
        "list": list_evidence,
        "get": {"status": "not_attempted"},
    }
    both_missing = len(attempts) == 2 and all(a["status"] == "not_found" for a in attempts)
    if both_missing:
        if not listing["complete"]:
            result.update(outcome=UNKNOWN, reason="list_incomplete")
        elif not key.startswith(LIST_PREFIX):
            result.update(outcome=UNKNOWN, reason="outside_jobs_list_prefix")
        elif listed:
            result.update(outcome=UNKNOWN, reason="head_list_disagreement")
        else:
            result.update(outcome=OBJECT_MISSING, reason="two_head_misses_and_full_list_absence")
        return result
    if any(attempt["status"] == UNKNOWN for attempt in attempts):
        result.update(outcome=UNKNOWN, reason="head_failed")
        return result
    if head is None:
        result.update(outcome=UNKNOWN, reason="head_inconclusive")
        return result
    if listing["complete"] and listed is False:
        result.update(outcome=UNKNOWN, reason="head_list_disagreement")
        return result

    budget = limits or ArtifactHashLimits()
    result["get"] = stream_hash(storage, key, budget, stored_size=head.size_bytes, clock=clock)
    if result["get"]["status"] != OK:
        reason = (
            "resource_budget_exceeded"
            if result["get"].get("reason")
            in {"stored_bytes_exceeded", "content_bytes_exceeded", "deadline_exceeded"}
            else "get_or_decode_failed"
        )
        result.update(outcome=UNKNOWN, reason=reason)
        return result
    expected_hash = str(row.get("content_hash") or "")
    actual_hash = str(result["get"]["content_sha256"])
    size_match = head.size_bytes == int(row["size_bytes"])
    result["size_match"] = size_match
    result["expected_content_sha256"] = expected_hash
    if expected_hash and actual_hash != expected_hash:
        result.update(outcome=HASH_MISMATCH, reason="streamed_content_sha256_mismatch")
    elif not size_match and not expected_hash:
        result.update(outcome=UNKNOWN, reason="size_mismatch_without_content_hash")
    elif not size_match:
        result.update(outcome=SIZE_MISMATCH, reason="head_size_mismatch_hash_match")
    else:
        result.update(
            outcome=OK, reason="sha256_verified" if expected_hash else "size_only_empty_hash"
        )
    return result
