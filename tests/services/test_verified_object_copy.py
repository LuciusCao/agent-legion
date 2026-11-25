"""Conditional-copy and post-copy verification contracts for #1553/#1548."""

from __future__ import annotations

import hashlib
import io

import pytest
from botocore.exceptions import ClientError

from server.app.services.verified_object_copy import (
    ObjectCopySpec,
    VerifiedObjectCopyFailure,
    VerifiedObjectCopyResult,
    copy_objects_verified,
)
from server.app.storage import ObjectHead, S3StorageClient
from server.app.storage.s3_settings import S3Settings
from tests.fakes.storage import FakeObjectStorage, etag_of

pytestmark = pytest.mark.no_db


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _spec(
    source_key: str,
    destination_key: str,
    payload: bytes,
    *,
    source_etag: str | None = None,
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> ObjectCopySpec:
    return ObjectCopySpec(
        source_key=source_key,
        destination_key=destination_key,
        source_etag=etag_of(payload) if source_etag is None else source_etag,
        expected_size_bytes=len(payload) if expected_size_bytes is None else expected_size_bytes,
        expected_sha256=_sha256(payload) if expected_sha256 is None else expected_sha256,
    )


def _failure(result: VerifiedObjectCopyResult) -> VerifiedObjectCopyFailure:
    assert not result.ok
    assert result.copies == ()
    assert result.failure is not None
    return result.failure


def test_s3_copy_source_etag_is_optional_and_quoted_on_the_wire() -> None:
    calls: list[dict[str, object]] = []

    class _FakeBoto:
        def copy_object(self, **kwargs: object) -> None:
            calls.append(kwargs)

    storage = S3StorageClient(
        S3Settings(
            bucket="materials-test",
            endpoint_url="http://rustfs:9000",
            access_key="ak",
            secret_key="sk",
        ),
        client=_FakeBoto(),
    )

    storage.copy_object("source/old", "target/old")
    storage.copy_object("source/pinned", "target/pinned", source_etag="abc123")

    assert "CopySourceIfMatch" not in calls[0]
    assert calls[1] == {
        "Bucket": "materials-test",
        "CopySource": {"Bucket": "materials-test", "Key": "source/pinned"},
        "Key": "target/pinned",
        "CopySourceIfMatch": '"abc123"',
    }


def test_fake_copy_matches_s3_missing_and_precondition_errors() -> None:
    storage = FakeObjectStorage(objects={"source": b"payload"})

    with pytest.raises(ClientError) as missing:
        storage.copy_object("missing", "target/missing")
    with pytest.raises(ClientError) as changed:
        storage.copy_object("source", "target/changed", source_etag="stale")

    assert missing.value.response["Error"]["Code"] == "404"
    assert changed.value.response["Error"]["Code"] == "412"
    assert "target/missing" not in storage.objects
    assert "target/changed" not in storage.objects

    storage.copy_object("source", "target/legacy")
    assert storage.objects["target/legacy"] == b"payload"
    assert storage.copy_calls[-1] == ("source", "target/legacy", "")


def test_verified_batch_returns_raw_identities_and_conditions_every_operation() -> None:
    first = b"first raw bytes"
    second = b"second raw bytes\x00\xff"
    storage = FakeObjectStorage(objects={"source/1": first, "source/2": second})

    result = copy_objects_verified(
        storage,
        [
            _spec("source/1", "target/version/1", first),
            _spec("source/2", "target/version/2", second),
        ],
    )

    assert result.ok
    assert result.failure is None
    assert [(item.destination_key, item.size_bytes, item.sha256) for item in result.copies] == [
        ("target/version/1", len(first), _sha256(first)),
        ("target/version/2", len(second), _sha256(second)),
    ]
    assert [item.object_etag for item in result.copies] == [etag_of(first), etag_of(second)]
    assert storage.copy_calls == [
        ("source/1", "target/version/1", etag_of(first)),
        ("source/2", "target/version/2", etag_of(second)),
    ]
    assert storage.stream_if_match == [etag_of(first), etag_of(second)]
    assert storage.objects["target/version/1"] == first
    assert storage.objects["target/version/2"] == second


def test_source_404_is_an_explicit_failure_and_cleans_attempted_destination() -> None:
    storage = FakeObjectStorage()

    result = copy_objects_verified(
        storage,
        [
            ObjectCopySpec(
                source_key="source/missing",
                destination_key="target/missing",
                source_etag="anything",
                expected_size_bytes=0,
                expected_sha256=_sha256(b""),
            )
        ],
    )

    failure = _failure(result)
    assert failure.code == "source_missing"
    assert failure.copy_index == 0
    assert storage.deleted == ["target/missing"]
    assert failure.cleanup_failures == ()


def test_source_etag_drift_fails_even_when_new_bytes_match_expected_content() -> None:
    old = b"old"
    new = b"new"
    storage = FakeObjectStorage(objects={"source": old})
    source_etag = etag_of(old)
    storage.objects["source"] = new

    result = copy_objects_verified(
        storage,
        [_spec("source", "target", new, source_etag=source_etag)],
    )

    failure = _failure(result)
    assert failure.code == "source_precondition_failed"
    assert storage.copy_calls == [("source", "target", source_etag)]
    assert "target" not in storage.objects


class _FailCopyAt(FakeObjectStorage):
    def __init__(
        self, objects: dict[str, bytes], fail_at: int, *, fail_after_write: bool = False
    ) -> None:
        super().__init__(objects=objects)
        self.fail_at = fail_at
        self.fail_after_write = fail_after_write

    def copy_object(self, source_key: str, destination_key: str, *, source_etag: str = "") -> None:
        if len(self.copy_calls) + 1 == self.fail_at:
            self.copy_calls.append((source_key, destination_key, source_etag))
            if self.fail_after_write:
                self.objects[destination_key] = self.objects[source_key]
            raise ClientError({"Error": {"Code": "InternalError"}}, "CopyObject")
        super().copy_object(source_key, destination_key, source_etag=source_etag)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
@pytest.mark.parametrize("fail_after_write", [False, True])
def test_kth_copy_failure_compensates_current_and_prior_destinations(
    fail_at: int, fail_after_write: bool
) -> None:
    payloads = [b"one", b"two", b"three"]
    sources = {f"source/{index}": payload for index, payload in enumerate(payloads)}
    storage = _FailCopyAt(sources, fail_at, fail_after_write=fail_after_write)
    specs = [
        _spec(f"source/{index}", f"target/{index}", payload)
        for index, payload in enumerate(payloads)
    ]

    result = copy_objects_verified(storage, specs)

    failure = _failure(result)
    assert failure.code == "copy_failed"
    assert failure.copy_index == fail_at - 1
    assert len(storage.copy_calls) == fail_at
    assert set(storage.deleted) == {f"target/{index}" for index in range(fail_at)}
    assert not any(key.startswith("target/") for key in storage.objects)
    assert all(storage.objects[key] == payload for key, payload in sources.items())


class _MissingDestinationHead(FakeObjectStorage):
    def head_object(self, storage_key: str) -> ObjectHead | None:
        if storage_key == "target/missing":
            return None
        return super().head_object(storage_key)


def test_destination_missing_after_copy_fails_and_cleans_it() -> None:
    storage = _MissingDestinationHead(objects={"source": b"payload"})

    result = copy_objects_verified(storage, [_spec("source", "target/missing", b"payload")])

    assert _failure(result).code == "destination_missing"
    assert storage.deleted == ["target/missing"]


class _ReadOverrideStorage(FakeObjectStorage):
    def __init__(
        self,
        *,
        source: bytes,
        read_payload: bytes,
        destination_head_size: int | None = None,
    ) -> None:
        super().__init__(objects={"source": source})
        self.read_payload = read_payload
        self.destination_head_size = destination_head_size

    def head_object(self, storage_key: str) -> ObjectHead | None:
        head = super().head_object(storage_key)
        if head is not None and storage_key == "target" and self.destination_head_size is not None:
            return ObjectHead(size_bytes=self.destination_head_size, etag=head.etag)
        return head

    def open_stream(self, storage_key: str, *, if_match: str = "") -> io.BytesIO:
        self.opened += 1
        self.stream_if_match.append(if_match)
        return io.BytesIO(self.read_payload)


def test_destination_head_size_check_cannot_be_replaced_by_stream_check() -> None:
    expected = b"expected"
    storage = _ReadOverrideStorage(source=b"x", read_payload=expected)

    result = copy_objects_verified(
        storage, [_spec("source", "target", expected, source_etag=etag_of(b"x"))]
    )

    assert _failure(result).code == "destination_size_mismatch"
    assert storage.opened == 0


def test_destination_stream_size_is_checked_independently_of_head_and_hash() -> None:
    streamed = b"four"
    storage = _ReadOverrideStorage(source=b"x", read_payload=streamed, destination_head_size=3)

    result = copy_objects_verified(
        storage,
        [
            _spec(
                "source",
                "target",
                streamed,
                source_etag=etag_of(b"x"),
                expected_size_bytes=3,
            )
        ],
    )

    failure = _failure(result)
    assert failure.code == "destination_size_mismatch"
    assert "stream size 4 != expected 3" in failure.detail


def test_destination_hash_mismatch_fails_after_conditional_streaming() -> None:
    actual = b"actual"
    expected_other = b"other!"
    assert len(actual) == len(expected_other)
    storage = FakeObjectStorage(objects={"source": actual})

    result = copy_objects_verified(
        storage,
        [_spec("source", "target", actual, expected_sha256=_sha256(expected_other))],
    )

    failure = _failure(result)
    assert failure.code == "destination_hash_mismatch"
    assert storage.stream_if_match == [etag_of(actual)]
    assert storage.deleted == ["target"]


class _ReplaceDestinationAfterHead(FakeObjectStorage):
    def __init__(self, *, old: bytes, new: bytes) -> None:
        super().__init__(objects={"source": old})
        self.new = new

    def head_object(self, storage_key: str) -> ObjectHead | None:
        head = super().head_object(storage_key)
        if storage_key == "target" and head is not None:
            self.objects[storage_key] = self.new
        return head


def test_destination_etag_drift_turns_conditional_get_into_412() -> None:
    old = b"old!!!"
    new = b"new!!!"
    storage = _ReplaceDestinationAfterHead(old=old, new=new)

    result = copy_objects_verified(
        storage,
        [_spec("source", "target", new, source_etag=etag_of(old))],
    )

    failure = _failure(result)
    assert failure.code == "destination_precondition_failed"
    assert storage.stream_if_match == [etag_of(old)]


def test_missing_destination_etag_is_fail_closed() -> None:
    payload = b"payload"
    storage = FakeObjectStorage(objects={"source": payload}, no_etag=True)

    result = copy_objects_verified(storage, [_spec("source", "target", payload)])

    assert _failure(result).code == "destination_etag_missing"
    assert storage.opened == 0


def test_cleanup_failure_is_reported_without_masking_copy_failure() -> None:
    storage = _FailCopyAt({"source": b"payload"}, fail_at=1, fail_after_write=True)
    storage.fail_deletes = True

    result = copy_objects_verified(storage, [_spec("source", "target", b"payload")])

    failure = _failure(result)
    assert failure.code == "copy_failed"
    assert len(failure.cleanup_failures) == 1
    cleanup = failure.cleanup_failures[0]
    assert cleanup.storage_key == "target"
    assert "ClientError" in cleanup.detail
    assert storage.objects["target"] == b"payload"


def test_duplicate_destination_and_missing_condition_are_rejected_before_any_write() -> None:
    payload = b"payload"
    storage = FakeObjectStorage(objects={"source": payload})

    duplicate = copy_objects_verified(
        storage,
        [_spec("source", "target/same", payload), _spec("source", "target/same", payload)],
    )
    unconditioned = copy_objects_verified(
        storage, [_spec("source", "target/other", payload, source_etag="")]
    )

    duplicate_failure = _failure(duplicate)
    assert duplicate_failure.code == "invalid_request"
    assert duplicate_failure.copy_index == 1
    assert "unique" in duplicate_failure.detail
    assert _failure(unconditioned).code == "invalid_request"
    assert storage.copy_calls == []
    assert storage.deleted == []
    assert storage.objects == {"source": payload}
