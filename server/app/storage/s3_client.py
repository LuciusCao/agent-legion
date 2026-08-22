"""S3-compatible object storage client (boto3).

The code programs against the S3 API only (presign/HEAD/GET/DELETE) — no
RustFS-specific features — so the backend can swap between RustFS, MinIO,
and AWS S3 by configuration alone (materials-and-runs design §6.6).

``ObjectStorage`` is the protocol the materials service depends on; tests
inject a fake implementation instead of touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, cast

from server.app.storage.s3_settings import S3Settings

_DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600


@dataclass(frozen=True)
class ObjectHead:
    """Metadata of a stored object."""

    size_bytes: int
    etag: str = ""


class ObjectStorage(Protocol):
    """Storage operations the materials service relies on."""

    def presign_put(
        self,
        storage_key: str,
        size_bytes: int,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        """Return a presigned PUT URL for direct client upload."""
        ...

    def head_object(self, storage_key: str) -> ObjectHead | None:
        """Return object metadata, or None when the object does not exist."""
        ...

    def presign_get(
        self,
        storage_key: str,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        """Return a presigned GET URL (Worker-side material downloads)."""
        ...

    def open_stream(self, storage_key: str) -> BinaryIO:
        """Return a streaming reader for the object bytes."""
        ...

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        """Store bytes directly (server-side writes: demo material seed)."""
        ...

    def delete_object(self, storage_key: str) -> None:
        """Delete the object; missing objects are not an error."""
        ...


class S3StorageClient:
    """boto3-backed ObjectStorage."""

    def __init__(self, settings: S3Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else _build_boto3_client(settings)

    def presign_put(
        self,
        storage_key: str,
        size_bytes: int,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        # SigV4 presigned PUT cannot constrain Content-Length (that requires a
        # presigned POST policy); size is enforced server-side at completion
        # via head_object instead.
        return str(
            self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._settings.bucket, "Key": storage_key},
                ExpiresIn=expires_seconds,
                HttpMethod="PUT",
            )
        )

    def presign_get(
        self,
        storage_key: str,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        # Issued on the claim-response path only (memory, never persisted):
        # the Worker downloads the material bytes over this URL and needs no
        # storage credentials of its own.
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._settings.bucket, "Key": storage_key},
                ExpiresIn=expires_seconds,
            )
        )

    def head_object(self, storage_key: str) -> ObjectHead | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.head_object(Bucket=self._settings.bucket, Key=storage_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectHead(
            size_bytes=int(response.get("ContentLength", 0)),
            etag=str(response.get("ETag", "")).strip('"'),
        )

    def open_stream(self, storage_key: str) -> BinaryIO:
        response = self._client.get_object(Bucket=self._settings.bucket, Key=storage_key)
        return cast(BinaryIO, response["Body"])

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        self._client.put_object(
            Bucket=self._settings.bucket,
            Key=storage_key,
            Body=data,
            **({"ContentType": content_type} if content_type else {}),
        )

    def delete_object(self, storage_key: str) -> None:
        self._client.delete_object(Bucket=self._settings.bucket, Key=storage_key)


def _build_boto3_client(settings: S3Settings) -> Any:
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    return boto3.client("s3", **kwargs)


def build_s3_storage() -> S3StorageClient | None:
    """Build the instance object store from env; None when not configured."""
    from server.app.storage.s3_settings import load_s3_settings

    settings = load_s3_settings()
    if settings is None:
        return None
    return S3StorageClient(settings)
