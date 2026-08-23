"""S3 settings loading: env-only infra config with _FILE secret variants."""

from __future__ import annotations

import pytest

from server.app.storage.s3_settings import _DEFAULT_REGION, load_s3_settings

_ENV_VARS = (
    "AGENT_LEGION_S3_BUCKET",
    "AGENT_LEGION_S3_ENDPOINT",
    "AGENT_LEGION_S3_REGION",
    "AGENT_LEGION_S3_ACCESS_KEY",
    "AGENT_LEGION_S3_ACCESS_KEY_FILE",
    "AGENT_LEGION_S3_SECRET_KEY",
    "AGENT_LEGION_S3_SECRET_KEY_FILE",
    "AGENT_LEGION_S3_PUBLIC_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_s3_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.no_db
def test_unconfigured_without_bucket() -> None:
    assert load_s3_settings() is None


@pytest.mark.no_db
def test_bucket_marks_configured_with_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-test")

    settings = load_s3_settings()

    assert settings is not None
    assert settings.bucket == "materials-test"
    assert settings.region == _DEFAULT_REGION
    assert settings.endpoint_url == ""
    assert settings.public_endpoint_url == ""
    assert settings.access_key == ""
    assert settings.secret_key == ""


@pytest.mark.no_db
def test_full_env_configuration(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-dev")
    monkeypatch.setenv("AGENT_LEGION_S3_ENDPOINT", "http://127.0.0.1:9000")
    monkeypatch.setenv("AGENT_LEGION_S3_REGION", "cn-north-1")
    monkeypatch.setenv("AGENT_LEGION_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("AGENT_LEGION_S3_SECRET_KEY", "sk")
    monkeypatch.setenv("AGENT_LEGION_S3_PUBLIC_ENDPOINT", "http://203.0.113.10:9000")

    settings = load_s3_settings()

    assert settings is not None
    assert settings.endpoint_url == "http://127.0.0.1:9000"
    assert settings.region == "cn-north-1"
    assert settings.access_key == "ak"
    assert settings.secret_key == "sk"
    assert settings.public_endpoint_url == "http://203.0.113.10:9000"


@pytest.mark.no_db
def test_secret_file_variant(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "s3_secret"
    secret_file.write_text("file-backed-secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-dev")
    monkeypatch.setenv("AGENT_LEGION_S3_SECRET_KEY_FILE", str(secret_file))

    settings = load_s3_settings()

    assert settings is not None
    assert settings.secret_key == "file-backed-secret"


@pytest.mark.no_db
def test_inline_value_wins_over_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "s3_secret"
    secret_file.write_text("file-backed-secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-dev")
    monkeypatch.setenv("AGENT_LEGION_S3_SECRET_KEY", "inline-secret")
    monkeypatch.setenv("AGENT_LEGION_S3_SECRET_KEY_FILE", str(secret_file))

    settings = load_s3_settings()

    assert settings is not None
    assert settings.secret_key == "inline-secret"
