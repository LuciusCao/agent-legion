"""Bounded raw/gzip streaming hash used by artifact recovery probes (#1507)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, BinaryIO, cast

from server.app.services.job_artifact_gzip import GunzipStream, is_gzip_key

OK = "ok"
UNKNOWN = "unknown"
HASH_CHUNK_BYTES = 1 << 20
DEFAULT_HASH_MAX_STORED_BYTES = 1 << 30
DEFAULT_HASH_MAX_CONTENT_BYTES = 4 << 30
DEFAULT_HASH_MAX_SECONDS = 60.0


@dataclass(frozen=True)
class ArtifactHashLimits:
    """Cumulative and wall-clock budget for one object's streamed hash."""

    max_stored_bytes: int = DEFAULT_HASH_MAX_STORED_BYTES
    max_content_bytes: int = DEFAULT_HASH_MAX_CONTENT_BYTES
    max_seconds: float | None = DEFAULT_HASH_MAX_SECONDS

    def __post_init__(self) -> None:
        if self.max_stored_bytes < 1 or self.max_content_bytes < 1:
            raise ValueError("artifact hash byte limits must be positive")
        if self.max_seconds is not None and self.max_seconds <= 0:
            raise ValueError("artifact hash deadline must be positive")

    def evidence(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_evidence(cls, evidence: Mapping[str, Any] | None) -> ArtifactHashLimits:
        values = dict(evidence or {})
        return cls(
            max_stored_bytes=int(values.get("max_stored_bytes", DEFAULT_HASH_MAX_STORED_BYTES)),
            max_content_bytes=int(values.get("max_content_bytes", DEFAULT_HASH_MAX_CONTENT_BYTES)),
            max_seconds=(
                float(values["max_seconds"])
                if values.get("max_seconds", DEFAULT_HASH_MAX_SECONDS) is not None
                else None
            ),
        )


def error_evidence(exc: Exception) -> dict[str, Any]:
    evidence: dict[str, Any] = {"type": type(exc).__name__}
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        metadata = response.get("ResponseMetadata")
        if isinstance(error, Mapping) and error.get("Code") is not None:
            evidence["code"] = str(error["Code"])
        if isinstance(metadata, Mapping) and metadata.get("HTTPStatusCode") is not None:
            evidence["http_status"] = int(metadata["HTTPStatusCode"])
    return evidence


class _HashBudgetExceeded(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _budget_failure(
    reason: str,
    limits: ArtifactHashLimits,
    stored_bytes: int,
    content_bytes: int,
) -> dict[str, Any]:
    return {
        "status": UNKNOWN,
        "reason": reason,
        "error": {"type": "ArtifactHashBudgetExceeded"},
        "limits": limits.evidence(),
        "stored_bytes_read": stored_bytes,
        "content_bytes_read": content_bytes,
    }


class _CountingStream:
    def __init__(
        self,
        raw: BinaryIO,
        limits: ArtifactHashLimits,
        started: float,
        clock: Callable[[], float],
    ) -> None:
        self._raw = raw
        self._limits = limits
        self._started = started
        self._clock = clock
        self.stored_bytes = 0

    def _check_deadline(self) -> None:
        if self._limits.max_seconds is not None and self._clock() - self._started > max(
            0.0, self._limits.max_seconds
        ):
            raise _HashBudgetExceeded("deadline_exceeded")

    def read(self, size: int = -1) -> bytes:
        self._check_deadline()
        chunk = self._raw.read(size)
        self.stored_bytes += len(chunk)
        if self.stored_bytes > self._limits.max_stored_bytes:
            raise _HashBudgetExceeded("stored_bytes_exceeded")
        self._check_deadline()
        return chunk

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> _CountingStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def stream_hash(
    storage: Any,
    key: str,
    limits: ArtifactHashLimits,
    *,
    stored_size: int,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Hash decoded content without ever accepting a partial/over-budget digest."""
    if stored_size > limits.max_stored_bytes:
        return _budget_failure("stored_bytes_exceeded", limits, 0, 0)
    digest = hashlib.sha256()
    content_bytes = 0
    started = clock()
    counting: _CountingStream | None = None
    try:
        raw = storage.open_stream(key)
        counting = _CountingStream(raw, limits, started, clock)
        stream = GunzipStream(cast(BinaryIO, counting)) if is_gzip_key(key) else counting
        with stream:
            while True:
                counting._check_deadline()
                chunk = stream.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                content_bytes += len(chunk)
                if content_bytes > limits.max_content_bytes:
                    raise _HashBudgetExceeded("content_bytes_exceeded")
                digest.update(chunk)
    except _HashBudgetExceeded as exc:
        return _budget_failure(
            exc.reason, limits, counting.stored_bytes if counting else 0, content_bytes
        )
    except Exception as exc:
        # #204 broad-except audit: GET open/read, gzip decode/truncation, and
        # stream close all fail closed as unknown. Partial digests are dropped;
        # only the sanitized exception identity is exposed in the report.
        return {"status": UNKNOWN, "error": error_evidence(exc)}
    return {
        "status": OK,
        "stored_form": "gzip" if is_gzip_key(key) else "raw",
        "chunk_bytes": HASH_CHUNK_BYTES,
        "stored_bytes_read": counting.stored_bytes if counting else 0,
        "content_bytes": content_bytes,
        "content_sha256": digest.hexdigest(),
        "limits": limits.evidence(),
    }
