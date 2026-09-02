"""Pure unit tests for the infra-connections summary builders (#335)."""

from __future__ import annotations

import pytest

from server.app.services.infra_connections import describe_database, describe_storage
from server.app.storage.s3_settings import S3Settings

pytestmark = pytest.mark.no_db


def test_describe_database_masks_password_and_drops_query() -> None:
    info = describe_database("postgresql://legion:secretpass@db.internal:5433/mydb?sslmode=require")

    assert info.engine == "postgresql"
    assert info.host == "db.internal"
    assert info.port == 5433
    assert info.name == "mydb"
    assert info.user == "legion"
    assert info.password_set is True
    assert info.masked_url == "postgresql://legion:***@db.internal:5433/mydb"
    assert "secretpass" not in info.masked_url
    # Query string dropped: connection options may themselves carry secrets.
    assert "sslmode" not in info.masked_url


def test_describe_database_without_password_keeps_plain_url() -> None:
    info = describe_database("postgresql://127.0.0.1:5432/agent_legion?options=x")

    assert info.user == ""
    assert info.password_set is False
    assert info.masked_url == "postgresql://127.0.0.1:5432/agent_legion"
    assert "***" not in info.masked_url


def test_describe_database_with_user_but_no_password() -> None:
    info = describe_database("postgresql://legion@db.internal/mydb")

    assert info.user == "legion"
    assert info.password_set is False
    assert info.port is None
    assert info.masked_url == "postgresql://legion@db.internal/mydb"


def test_describe_database_rewraps_ipv6_host() -> None:
    """urlsplit's .hostname strips the IPv6 brackets; the masked URL must
    re-wrap them or it would not round-trip as a recognizable DSN."""
    info = describe_database("postgresql://legion:secretpass@[2001:db8::10]:5433/mydb")

    assert info.host == "2001:db8::10"
    assert info.port == 5433
    assert info.masked_url == "postgresql://legion:***@[2001:db8::10]:5433/mydb"
    assert "secretpass" not in info.masked_url


def test_describe_database_ipv6_without_port_or_user() -> None:
    info = describe_database("postgresql://[::1]/mydb")

    assert info.host == "::1"
    assert info.port is None
    assert info.password_set is False
    assert info.masked_url == "postgresql://[::1]/mydb"


def test_describe_database_degrades_on_garbage_dsn() -> None:
    for garbage in ("not-a-dsn", "", "postgresql://u:p@h:notaport/db"):
        info = describe_database(garbage)
        assert info.engine == "unknown"
        assert info.masked_url == "***"
        assert info.password_set is False
        # The raw DSN (which may embed a password) never leaks through.
        if garbage:
            assert garbage not in info.masked_url


def test_describe_storage_unconfigured_never_reachable() -> None:
    info = describe_storage(None, reachable=True)

    assert info.configured is False
    assert info.credentials == "unconfigured"
    assert info.reachable is False
    assert info.bucket == ""
    assert info.backend == ""


def test_describe_storage_static_credentials() -> None:
    settings = S3Settings(
        bucket="materials",
        endpoint_url="http://rustfs:9000",
        region="cn-test-1",
        access_key="AKID",
        secret_key="SECRET",
        public_endpoint_url="http://localhost:9100",
    )

    info = describe_storage(settings, reachable=True)

    assert info.configured is True
    assert info.backend == "RustFS"
    assert info.endpoint_url == "http://rustfs:9000"
    assert info.public_endpoint_url == "http://localhost:9100"
    assert info.bucket == "materials"
    assert info.region == "cn-test-1"
    assert info.credentials == "static"
    assert info.reachable is True


def test_describe_storage_default_chain_when_no_access_key() -> None:
    settings = S3Settings(bucket="materials")

    info = describe_storage(settings, reachable=False)

    assert info.credentials == "default-chain"
    assert info.reachable is False
    # Empty endpoint targets the AWS S3 default endpoint.
    assert info.backend == "AWS S3"


@pytest.mark.parametrize(
    ("endpoint_url", "expected"),
    [
        ("http://seaweedfs:8333", "SeaweedFS"),
        ("http://rustfs:9000", "RustFS"),
        ("https://minio.example.com", "MinIO"),
        ("https://s3.amazonaws.com", "AWS S3"),
        ("https://s3.cn-north-1.amazonaws.com.cn", "AWS S3"),
        ("http://s3.internal:9000", "S3 兼容（s3.internal）"),
        # Unparseable input (no scheme → no hostname): degrade without empty parens.
        ("rustfs:9000", "S3 兼容"),
        ("", "AWS S3"),
    ],
)
def test_infer_storage_backend_labels(endpoint_url: str, expected: str) -> None:
    """The backend label is display-only, inferred from the endpoint host."""
    settings = S3Settings(bucket="materials", endpoint_url=endpoint_url)

    assert describe_storage(settings, reachable=False).backend == expected
