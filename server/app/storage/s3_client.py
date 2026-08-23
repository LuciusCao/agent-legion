"""S3-compatible object storage client (boto3).

Programs against the S3 API only (presign/HEAD/GET/DELETE) so the backend
swaps between RustFS / MinIO / AWS S3 by configuration alone
(materials-and-runs design §6.6). ``ObjectStorage`` is the protocol the
materials service depends on; tests inject a fake instead of the network.
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
        self._presign_client: Any | None = None

    def _signing_client(self) -> Any:
        """Client used to presign URLs handed to browsers / remote workers.

        With a public endpoint configured the URL is signed against it via a
        dedicated client (not host rewriting — SigV4 signs the Host header).
        """
        public = self._settings.public_endpoint_url
        if public and self._presign_client is None:
            self._presign_client = _build_boto3_client(self._settings, endpoint_override=public)
        return self._presign_client if public else self._client

    def _presign(self, operation: str, storage_key: str, expires_seconds: int, **extra: Any) -> str:
        return str(
            self._signing_client().generate_presigned_url(
                operation,
                Params={"Bucket": self._settings.bucket, "Key": storage_key},
                ExpiresIn=expires_seconds,
                **extra,
            )
        )

    def presign_put(
        self,
        storage_key: str,
        size_bytes: int,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        # SigV4 presigned PUT cannot constrain Content-Length (that requires a
        # presigned POST policy); size is enforced server-side at completion
        # via head_object instead.
        return self._presign("put_object", storage_key, expires_seconds, HttpMethod="PUT")

    def presign_get(
        self,
        storage_key: str,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> str:
        # Issued on the claim-response path only (memory, never persisted):
        # the Worker downloads the material bytes over this URL and needs no
        # storage credentials of its own.
        return self._presign("get_object", storage_key, expires_seconds)

    def head_object(self, storage_key: str) -> ObjectHead | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.head_object(Bucket=self._settings.bucket, Key=storage_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        size = int(response.get("ContentLength", 0))
        return ObjectHead(size_bytes=size, etag=str(response.get("ETag", "")).strip('"'))

    def open_stream(self, storage_key: str) -> BinaryIO:
        response = self._client.get_object(Bucket=self._settings.bucket, Key=storage_key)
        return cast(BinaryIO, response["Body"])

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self._settings.bucket, Key=storage_key, Body=data, **extra)

    def delete_object(self, storage_key: str) -> None:
        self._client.delete_object(Bucket=self._settings.bucket, Key=storage_key)


def _build_boto3_client(settings: S3Settings, *, endpoint_override: str | None = None) -> Any:
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.region}
    if endpoint := (endpoint_override if endpoint_override is not None else settings.endpoint_url):
        kwargs["endpoint_url"] = endpoint
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
