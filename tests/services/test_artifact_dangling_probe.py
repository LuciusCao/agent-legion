"""Object evidence matrix for the read-only artifact recovery probe (#1019)."""

from __future__ import annotations

import gzip
import hashlib
import io
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from botocore.exceptions import ClientError

from server.app.services.artifact_dangling_probe import (
    HASH_MISMATCH,
    OBJECT_MISSING,
    OK,
    UNKNOWN,
    ArtifactHashLimits,
    probe_artifact,
    probe_storage,
)
from server.app.storage.s3_client import ObjectHead, S3Settings, S3StorageClient

pytestmark = pytest.mark.no_db


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


class FakeStorage:
    def __init__(
        self,
        objects: Mapping[str, bytes] | None = None,
        *,
        pages: list[list[str] | Exception] | None = None,
        head_errors: Mapping[str, Exception] | None = None,
        get_errors: Mapping[str, Exception] | None = None,
        head_bucket_error: Exception | None = None,
    ) -> None:
        self.objects = dict(objects or {})
        self.pages = pages
        self.head_errors = dict(head_errors or {})
        self.get_errors = dict(get_errors or {})
        self.head_bucket_error = head_bucket_error
        self.head_calls: list[str] = []
        self.get_calls: list[str] = []
        self.list_calls = 0
        self.head_bucket_calls = 0

    def configuration_fingerprint(self) -> dict[str, str]:
        return {
            "algorithm": "sha256",
            "bucket": "b" * 64,
            "endpoint": "e" * 64,
            "public_endpoint": "p" * 64,
            "region": "r" * 64,
            "configuration": "c" * 64,
        }

    def head_bucket(self) -> None:
        self.head_bucket_calls += 1
        if self.head_bucket_error is not None:
            raise self.head_bucket_error

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]:
        self.list_calls += 1
        if self.pages is not None:
            for page in self.pages:
                if isinstance(page, Exception):
                    raise page
                yield page
            return
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        for start in range(0, len(keys), page_size):
            yield keys[start : start + page_size]

    def head_object(self, storage_key: str) -> ObjectHead | None:
        self.head_calls.append(storage_key)
        if storage_key in self.head_errors:
            raise self.head_errors[storage_key]
        if storage_key not in self.objects:
            return None
        return ObjectHead(size_bytes=len(self.objects[storage_key]), etag="etag")

    def open_stream(self, storage_key: str) -> io.BytesIO:
        self.get_calls.append(storage_key)
        if storage_key in self.get_errors:
            raise self.get_errors[storage_key]
        return io.BytesIO(self.objects[storage_key])


def _row(
    key: str,
    content: bytes,
    *,
    size: int | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "node_key": "producer",
        "storage_key": key,
        "size_bytes": len(content) if size is None else size,
        "content_hash": hashlib.sha256(content).hexdigest()
        if content_hash is None
        else content_hash,
        "uploaded_at": "2026-01-01T00:00:00+00:00",
    }


def test_object_missing_needs_two_heads_and_complete_multipage_list() -> None:
    key = "jobs/ws/job/missing.json"
    row = _row(key, b"expected")
    storage = FakeStorage(pages=[["jobs/ws/other/a"], ["jobs/ws/other/b"]])
    storage_probe = probe_storage(storage, [key])

    result = probe_artifact(storage, row, storage_probe)

    assert result["outcome"] == OBJECT_MISSING
    assert result["reason"] == "two_head_misses_and_full_list_absence"
    assert storage.head_calls == [key, key]
    assert storage.get_calls == []
    assert storage_probe.evidence["listing"]["page_count"] == 2
    assert storage_probe.evidence["listing"]["complete"] is True
    assert result["list"] == {"complete": True, "key_present": False, "within_prefix": True}


def test_head_miss_without_complete_list_or_list_agreement_is_unknown() -> None:
    key = "jobs/ws/job/missing.json"
    row = _row(key, b"expected")
    for pages, reason in (
        ([["jobs/ws/other"], TimeoutError("list timeout")], "list_incomplete"),
        ([[key]], "head_list_disagreement"),
    ):
        storage = FakeStorage(pages=pages)
        result = probe_artifact(storage, row, probe_storage(storage, [key]))
        assert result["outcome"] == UNKNOWN
        assert result["reason"] == reason
        assert storage.get_calls == []


def test_raw_hash_match_and_same_size_different_bytes() -> None:
    key = "jobs/ws/job/raw.json"
    storage = FakeStorage({key: b"abc"})
    storage_probe = probe_storage(storage, [key])

    matched = probe_artifact(storage, _row(key, b"abc"), storage_probe)
    assert matched["outcome"] == OK
    assert matched["get"]["content_sha256"] == hashlib.sha256(b"abc").hexdigest()

    storage.objects[key] = b"xyz"
    mismatched = probe_artifact(storage, _row(key, b"abc"), storage_probe)
    assert mismatched["outcome"] == HASH_MISMATCH
    assert mismatched["size_match"] is True
    assert mismatched["reason"] == "streamed_content_sha256_mismatch"


def test_gzip_hash_uses_uncompressed_bytes_and_compressed_size() -> None:
    key = "jobs/ws/job/raw.json.gz"
    raw = b"hello artifact" * 100
    stored = gzip.compress(raw, mtime=0)
    storage = FakeStorage({key: stored})
    storage_probe = probe_storage(storage, [key])

    result = probe_artifact(storage, _row(key, raw, size=len(stored)), storage_probe)

    assert result["outcome"] == OK
    assert result["get"]["stored_form"] == "gzip"
    assert result["get"]["content_bytes"] == len(raw)
    assert result["get"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["size_match"] is True


def test_empty_hash_is_size_only_and_unverifiable_size_mismatch_is_unknown() -> None:
    key = "jobs/ws/job/legacy.json"
    storage = FakeStorage({key: b"abc"})
    storage_probe = probe_storage(storage, [key])

    result = probe_artifact(storage, _row(key, b"abc", content_hash=""), storage_probe)
    assert result["outcome"] == OK
    assert result["reason"] == "size_only_empty_hash"
    storage.objects[key] = b"xyz"
    assert (
        probe_artifact(storage, _row(key, b"abc", content_hash=""), storage_probe)["outcome"] == OK
    )

    unknown = probe_artifact(storage, _row(key, b"abc", size=4, content_hash=""), storage_probe)
    assert unknown["outcome"] == UNKNOWN
    assert unknown["reason"] == "size_mismatch_without_content_hash"


def test_hash_match_with_wrong_head_size_is_reported_separately() -> None:
    key = "jobs/ws/job/size.json"
    storage = FakeStorage({key: b"abc"})
    result = probe_artifact(storage, _row(key, b"abc", size=4), probe_storage(storage, [key]))
    assert result["outcome"] == "size_mismatch"
    assert result["reason"] == "head_size_mismatch_hash_match"


@pytest.mark.parametrize(
    "error",
    [
        _client_error("403", 403),
        _client_error("InternalError", 500),
        TimeoutError("head timeout"),
    ],
)
def test_head_failures_are_unknown(error: Exception) -> None:
    key = "jobs/ws/job/raw.json"
    storage = FakeStorage({key: b"abc"}, head_errors={key: error})
    result = probe_artifact(storage, _row(key, b"abc"), probe_storage(storage, [key]))
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "head_failed"
    assert storage.head_calls == [key]
    assert storage.get_calls == []


@pytest.mark.parametrize(
    "error",
    [
        _client_error("403", 403),
        _client_error("InternalError", 500),
        TimeoutError("get timeout"),
    ],
)
def test_get_failures_are_unknown(error: Exception) -> None:
    key = "jobs/ws/job/raw.json"
    storage = FakeStorage({key: b"abc"}, get_errors={key: error})
    result = probe_artifact(storage, _row(key, b"abc"), probe_storage(storage, [key]))
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "get_or_decode_failed"
    assert result["get"]["status"] == UNKNOWN
    assert "content_sha256" not in result["get"]


def test_gzip_decode_failure_is_unknown_not_actionable_corrupt() -> None:
    key = "jobs/ws/job/bad.json.gz"
    storage = FakeStorage({key: b"not a gzip stream"})
    result = probe_artifact(
        storage,
        _row(key, b"expected", size=len(b"not a gzip stream")),
        probe_storage(storage, [key]),
    )
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "get_or_decode_failed"


def test_storage_health_sanitizes_errors_and_configuration() -> None:
    storage = FakeStorage(head_bucket_error=_client_error("403", 403))
    result = probe_storage(storage, ["jobs/ws/job/a"])
    assert result.evidence["head_bucket"]["status"] == UNKNOWN
    assert result.evidence["head_bucket"]["error"]["http_status"] == 403
    assert result.evidence["configuration"]["bucket"] == "b" * 64
    assert "secret" not in str(result.evidence).lower()


class _BotoListClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_paginator(self, name: str) -> _BotoListClient:
        assert name == "list_objects_v2"
        return self

    def paginate(self, **params: Any) -> Iterator[dict[str, Any]]:
        self.calls.append(params)
        yield {"Contents": [{"Key": "jobs/a"}]}
        yield {"Contents": [{"Key": "jobs/b"}]}

    def head_bucket(self, **params: Any) -> None:
        self.calls.append(params)


def test_s3_client_streams_list_pages_and_hashes_configuration() -> None:
    from server.app.services.artifact_dangling_probe import (
        S3ArtifactDanglingStorage,
        s3_configuration_fingerprint,
    )

    client = _BotoListClient()
    settings = S3Settings(
        bucket="private-bucket",
        endpoint_url="https://private-endpoint.invalid",
        access_key="secret-access",
        secret_key="secret-key",
    )
    storage = S3ArtifactDanglingStorage(settings, client=client)

    assert isinstance(storage, S3StorageClient)
    assert list(storage.iter_object_pages("jobs/", page_size=1)) == [["jobs/a"], ["jobs/b"]]
    assert client.calls[0]["PaginationConfig"] == {"PageSize": 1}
    storage.head_bucket()
    fingerprint = s3_configuration_fingerprint(settings)
    assert fingerprint["algorithm"] == "sha256"
    assert len(fingerprint["configuration"]) == 64
    assert "private-bucket" not in str(fingerprint)
    assert "private-endpoint" not in str(fingerprint)
    assert "secret-access" not in str(fingerprint)
    assert "secret-key" not in str(fingerprint)


def test_hash_limits_allow_the_exact_raw_and_gzip_boundaries() -> None:
    limits = ArtifactHashLimits(max_stored_bytes=128, max_content_bytes=16, max_seconds=None)
    for key, content in (("jobs/ws/job/raw", b"exact"), ("jobs/ws/job/gzip.gz", b"exact")):
        stored = gzip.compress(content, mtime=0) if key.endswith(".gz") else content
        storage = FakeStorage({key: stored})
        result = probe_artifact(
            storage,
            _row(key, content, size=len(stored)),
            probe_storage(storage, [key]),
            limits=limits,
        )
        assert result["outcome"] == OK
        assert result["get"]["stored_bytes_read"] == len(stored)
        assert result["get"]["content_bytes"] == len(content)


def test_stored_head_over_limit_skips_get_and_fails_closed() -> None:
    key = "jobs/ws/job/large"
    storage = FakeStorage({key: b"12345"})
    result = probe_artifact(
        storage,
        _row(key, b"12345"),
        probe_storage(storage, [key]),
        limits=ArtifactHashLimits(max_stored_bytes=4, max_content_bytes=100),
    )
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "resource_budget_exceeded"
    assert result["get"]["reason"] == "stored_bytes_exceeded"
    assert result["get"]["stored_bytes_read"] == 0
    assert "content_sha256" not in result["get"]
    assert storage.get_calls == []


def test_gzip_expansion_budget_discards_partial_hash() -> None:
    key = "jobs/ws/job/bomb.gz"
    raw = b"a" * 128
    stored = gzip.compress(raw, mtime=0)
    assert len(stored) < 64
    storage = FakeStorage({key: stored})
    result = probe_artifact(
        storage,
        _row(key, raw, size=len(stored)),
        probe_storage(storage, [key]),
        limits=ArtifactHashLimits(max_stored_bytes=64, max_content_bytes=32),
    )
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "resource_budget_exceeded"
    assert result["get"]["reason"] == "content_bytes_exceeded"
    assert result["get"]["content_bytes_read"] == len(raw)
    assert "content_sha256" not in result["get"]


def test_continuous_stream_is_cut_off_by_cumulative_stored_budget() -> None:
    key = "jobs/ws/job/endless"

    class EndlessStorage(FakeStorage):
        def open_stream(self, storage_key: str):
            self.get_calls.append(storage_key)

            class Endless:
                def __init__(self) -> None:
                    self.reads = 0
                    self.closed = False

                def read(self, _size: int) -> bytes:
                    self.reads += 1
                    return b"x" * 1024

                def close(self) -> None:
                    self.closed = True

            self.endless = Endless()
            return self.endless

    storage = EndlessStorage({key: b"head"})
    result = probe_artifact(
        storage,
        _row(key, b"head"),
        probe_storage(storage, [key]),
        limits=ArtifactHashLimits(
            max_stored_bytes=4096, max_content_bytes=1 << 20, max_seconds=None
        ),
    )
    assert result["outcome"] == UNKNOWN
    assert result["get"]["reason"] == "stored_bytes_exceeded"
    assert result["get"]["stored_bytes_read"] == 5120
    assert storage.endless.reads == 5
    assert storage.endless.closed is True


def test_hash_deadline_is_unknown_and_never_accepts_partial_content() -> None:
    key = "jobs/ws/job/slow"
    storage = FakeStorage({key: b"slow"})
    ticks = iter([0.0, 0.5, 2.0])
    result = probe_artifact(
        storage,
        _row(key, b"slow"),
        probe_storage(storage, [key]),
        limits=ArtifactHashLimits(max_stored_bytes=10, max_content_bytes=10, max_seconds=1),
        clock=lambda: next(ticks),
    )
    assert result["outcome"] == UNKNOWN
    assert result["reason"] == "resource_budget_exceeded"
    assert result["get"]["reason"] == "deadline_exceeded"
    assert "content_sha256" not in result["get"]
