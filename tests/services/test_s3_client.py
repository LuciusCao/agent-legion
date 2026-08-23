"""S3StorageClient presigning: internal vs public endpoint URL selection.

Presigned URLs are generated offline (boto3 signing needs no network), so
these tests build real boto3 clients with fake credentials and assert on the
signed URL's host.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from server.app.storage.s3_client import S3StorageClient
from server.app.storage.s3_settings import S3Settings

pytestmark = pytest.mark.no_db

_INTERNAL = "http://rustfs:9000"
_PUBLIC = "http://127.0.0.1:9000"


def _settings(**overrides: str) -> S3Settings:
    base = {
        "bucket": "materials-test",
        "endpoint_url": _INTERNAL,
        "access_key": "ak",
        "secret_key": "sk",
    }
    base.update(overrides)
    return S3Settings(**base)  # type: ignore[arg-type]


def test_presign_uses_internal_endpoint_without_public_override() -> None:
    storage = S3StorageClient(_settings())

    for url in (storage.presign_put("k.bin", 10), storage.presign_get("k.bin")):
        assert urlparse(url).netloc == urlparse(_INTERNAL).netloc


def test_presign_uses_public_endpoint_when_configured() -> None:
    storage = S3StorageClient(_settings(public_endpoint_url=_PUBLIC))

    put_url = storage.presign_put("k.bin", 10)
    get_url = storage.presign_get("k.bin")

    for url in (put_url, get_url):
        assert urlparse(url).netloc == urlparse(_PUBLIC).netloc
    # Non-presign operations (HEAD/GET/DELETE) still use the internal endpoint.
    assert storage._client.meta.endpoint_url == _INTERNAL


def test_presign_signature_is_present_on_public_urls() -> None:
    storage = S3StorageClient(_settings(public_endpoint_url=_PUBLIC))

    url = storage.presign_put("k.bin", 10)

    # SigV2 (old botocore) signs with "Signature=", SigV4 with "X-Amz-Signature=".
    assert "Signature=" in url
