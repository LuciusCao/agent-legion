"""ConnectionService: CRUD, secret diversion/masking, validation, probe."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.connections import ConnectionService, connection_secret_name
from server.app.services.instance_vault import InstanceVaultService
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.fixture
def service(job_db, settings, vault_key):
    return ConnectionService(job_db.dsn_identity, settings.config)


def _create_static(service, key="cms-internal", token="tok-123"):
    return service.create(key, "static_bearer", "CMS", {"base_url": "http://x", "token": token})


def test_create_diverts_secret_and_masks_view(service, job_db, settings) -> None:
    view = _create_static(service)

    assert view["type"] == "static_bearer"
    assert view["config"]["base_url"] == "http://x"
    assert view["config"]["token"] == {"secret_set": True}
    # The stored row carries a ref marker, never plaintext (VAULT-SECRET-001).
    raw = service._decode_config(service._row("cms-internal"))
    assert raw["token"] == {"secret_ref": "conn:cms-internal:token"}
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) == "tok-123"


def test_create_validates_key_and_type(service) -> None:
    with pytest.raises(InvalidOperationError):
        service.create("Bad Key", "static_bearer", "", {})
    with pytest.raises(InvalidOperationError):
        service.create("ok-key", "no_such_type", "", {})


def test_create_conflict(service) -> None:
    _create_static(service)
    with pytest.raises(ConflictError):
        _create_static(service)


def test_update_secret_echo_keeps_stored_value(service, job_db, settings) -> None:
    _create_static(service)
    view = service.update(
        "cms-internal", config={"base_url": "http://y", "token": {"secret_set": True}}
    )

    assert view["config"]["base_url"] == "http://y"
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) == "tok-123"


def test_update_omitted_secret_is_inherited(service, job_db, settings) -> None:
    """A secret field left out of the update payload keeps its stored value
    (省略即保留, mirroring workspace node config semantics)."""
    _create_static(service)
    view = service.update("cms-internal", config={"base_url": "http://y"})

    assert view["config"]["base_url"] == "http://y"
    assert view["config"]["token"] == {"secret_set": True}
    raw = service._decode_config(service._row("cms-internal"))
    assert raw["token"] == {"secret_ref": "conn:cms-internal:token"}
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) == "tok-123"


def test_update_empty_secret_clears_vault_entry(service, job_db, settings) -> None:
    """An explicit empty string clears the credential and removes the vault
    entry with the same commit (no orphans)."""
    _create_static(service)
    view = service.update("cms-internal", config={"base_url": "http://y", "token": ""})

    assert "token" not in view["config"]
    raw = service._decode_config(service._row("cms-internal"))
    assert "token" not in raw
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) is None


def test_create_rolls_back_row_and_vault_on_failure(service, job_db, settings, monkeypatch) -> None:
    """A failure mid-create leaves neither the connection row nor vault entries."""

    def _boom(conn, name, ciphertext) -> None:
        raise RuntimeError("simulated vault failure")

    monkeypatch.setattr(InstanceVaultService, "set_in", _boom)
    with pytest.raises(RuntimeError, match="simulated vault failure"):
        _create_static(service)

    with pytest.raises(NotFoundError):
        service.get("cms-internal")
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) is None


def test_update_config_invalidates_cached_token(service, job_db, settings) -> None:
    _create_static(service)
    tokens = ConnectionTokenService(job_db.dsn_identity, settings.config)
    assert tokens.get_token("cms-internal") == "tok-123"

    service.update("cms-internal", config={"base_url": "http://y", "token": "tok-456"})

    assert tokens.get_token("cms-internal") == "tok-456"


def test_delete_removes_vault_entries(service, job_db, settings) -> None:
    _create_static(service)
    service.delete("cms-internal")

    with pytest.raises(NotFoundError):
        service.get("cms-internal")
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) is None


def test_probe_without_probe_url(service) -> None:
    _create_static(service)
    result = service.probe("cms-internal")
    assert result["ok"] is True
    assert "跳过连通性探测" in result["message"]


def test_probe_disabled_connection_rejected(service) -> None:
    _create_static(service)
    service.update("cms-internal", enabled=False)
    with pytest.raises(InvalidOperationError, match="停用"):
        service.probe("cms-internal")


def test_runtime_config_exposes_public_config_plus_token(service, job_db, settings) -> None:
    _create_static(service)
    # Public config never carries secret material; the token joins only via
    # the token service (freshly validated/refreshed).
    public = service.resolve_public_config("cms-internal")
    assert public == {"base_url": "http://x"}
    tokens = ConnectionTokenService(job_db.dsn_identity, settings.config)
    runtime = tokens.runtime_config("cms-internal")
    assert runtime["token"] == "tok-123"
    assert runtime["base_url"] == "http://x"


def test_conflicting_create_preserves_existing_secret(service, job_db, settings) -> None:
    _create_static(service, token="tok-original")
    with pytest.raises(ConflictError):
        _create_static(service, token="tok-evil")
    vault = InstanceVaultService(job_db.dsn_identity, settings.config)
    assert vault.get(connection_secret_name("cms-internal", "token")) == "tok-original"


def test_create_secret_without_master_key_fails_before_writes(
    job_db, settings, monkeypatch
) -> None:
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    # settings.config may carry vault.master_key(_file); strip both.
    settings.config.pop("vault", None)
    service = ConnectionService(job_db.dsn_identity, settings.config)
    with pytest.raises(InvalidOperationError, match="master key"):
        _create_static(service)
    with pytest.raises(NotFoundError):
        service.get("cms-internal")
