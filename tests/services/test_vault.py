"""Vault service: encryption round-trip, key handling, secret_ref resolution."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.services.job_intake import JobIntakeService
from server.app.services.vault import (
    VaultError,
    VaultMasterKeyMissingError,
    VaultService,
)
from server.app.services.vault_resources import (
    apply_resource_secret_fields,
    resource_secret_name,
    strip_resource_secret_fields,
)
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.cms_helpers import _effective_cms_config

PLAINTEXT = "s3cr3t-cms-token"


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.fixture
def vault(job_db, settings, vault_key):
    return VaultService(job_db.path, settings.config)


def _resource_config(token_value: object) -> dict:
    return {
        "resources": {
            "question_detail": {
                "enabled": True,
                "config": {
                    "api_url": "http://cms.example.com/question/detail",
                    "token": token_value,
                },
            }
        }
    }


def test_set_get_round_trip(vault, job_db):
    workspace = job_db.create_workspace("vault-roundtrip")
    metadata = vault.set(workspace["id"], "api-token", PLAINTEXT)

    assert metadata["name"] == "api-token"
    assert metadata["created_at"]
    assert metadata["updated_at"]
    assert vault.get(workspace["id"], "api-token") == PLAINTEXT


def test_set_overwrites_existing(vault, job_db):
    workspace = job_db.create_workspace("vault-overwrite")
    vault.set(workspace["id"], "api-token", "first")
    vault.set(workspace["id"], "api-token", "second")
    assert vault.get(workspace["id"], "api-token") == "second"


def test_get_missing_returns_none(vault, job_db):
    workspace = job_db.create_workspace("vault-missing")
    assert vault.get(workspace["id"], "nope") is None


def test_list_returns_metadata_only(vault, job_db):
    workspace = job_db.create_workspace("vault-list")
    vault.set(workspace["id"], "b-token", "value-b")
    vault.set(workspace["id"], "a-token", "value-a")

    entries = vault.list(workspace["id"])

    assert [entry["name"] for entry in entries] == ["a-token", "b-token"]
    for entry in entries:
        assert set(entry) == {"name", "created_at", "updated_at"}
    assert PLAINTEXT not in json.dumps(entries)
    assert "value-a" not in json.dumps(entries)


def test_delete_removes_entry(vault, job_db):
    workspace = job_db.create_workspace("vault-delete")
    vault.set(workspace["id"], "api-token", PLAINTEXT)
    vault.delete(workspace["id"], "api-token")
    assert vault.get(workspace["id"], "api-token") is None
    assert vault.list(workspace["id"]) == []


def test_ciphertext_is_not_plaintext(vault, job_db):
    workspace = job_db.create_workspace("vault-cipher")
    vault.set(workspace["id"], "api-token", PLAINTEXT)
    with job_db.connect() as conn:
        row = conn.execute(
            "select ciphertext from workspace_secrets where workspace_id=? and name=?",
            (workspace["id"], "api-token"),
        ).fetchone()
    assert row["ciphertext"] != PLAINTEXT
    assert PLAINTEXT not in row["ciphertext"]


def test_missing_master_key_blocks_writes_and_reads(job_db, settings, monkeypatch):
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    vault = VaultService(job_db.path, {})
    workspace = job_db.create_workspace("vault-no-key")

    with pytest.raises(VaultMasterKeyMissingError, match="AGENT_LEGION_VAULT_MASTER_KEY"):
        vault.set(workspace["id"], "api-token", PLAINTEXT)


def test_invalid_master_key_rejected(job_db, monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", "not-a-fernet-key")
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    vault = VaultService(job_db.path, {})
    with pytest.raises(VaultError, match="not a valid Fernet key"):
        vault.set("ws", "api-token", PLAINTEXT)


def test_resolve_secret_refs_replaces_ref_and_passes_plaintext(vault, job_db):
    workspace = job_db.create_workspace("vault-resolve")
    name = resource_secret_name("question_detail", "token")
    vault.set(workspace["id"], name, PLAINTEXT)

    resolved = vault.resolve_secret_refs(
        {"token": {"secret_ref": name}, "legacy": "legacy-plain", "api_url": "http://x"},
        workspace["id"],
    )

    assert resolved["token"] == PLAINTEXT
    assert resolved["legacy"] == "legacy-plain"
    assert resolved["api_url"] == "http://x"


def test_resolve_secret_refs_missing_entry_raises(vault, job_db):
    workspace = job_db.create_workspace("vault-resolve-missing")
    with pytest.raises(VaultError, match="not found"):
        vault.resolve_secret_refs({"token": {"secret_ref": "gone"}}, workspace["id"])


def test_apply_resource_secret_fields_diverts_to_vault(vault, job_db, settings):
    workspace = job_db.create_workspace("vault-apply")
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {"api_url": "http://cms.example.com/q", "token": PLAINTEXT},
        }
    }

    applied = apply_resource_secret_fields(
        vault, workspace["id"], resources, {}, settings.resource_providers.schemas
    )

    token = applied["question_detail"]["config"]["token"]
    assert token == {"secret_ref": "resource:question_detail:token"}
    assert vault.get(workspace["id"], "resource:question_detail:token") == PLAINTEXT


def test_apply_resource_secret_fields_empty_value_clears(vault, job_db, settings):
    workspace = job_db.create_workspace("vault-clear")
    current = _resource_config({"secret_ref": "resource:question_detail:token"})["resources"]
    vault.set(workspace["id"], "resource:question_detail:token", PLAINTEXT)
    patch = {"question_detail": {"enabled": True, "config": {"token": ""}}}

    applied = apply_resource_secret_fields(
        vault, workspace["id"], patch, current, settings.resource_providers.schemas
    )

    assert "token" not in applied["question_detail"]["config"]
    assert vault.get(workspace["id"], "resource:question_detail:token") is None


def test_apply_resource_secret_fields_absent_key_inherits_stored(vault, job_db, settings):
    workspace = job_db.create_workspace("vault-inherit")
    current = _resource_config({"secret_ref": "resource:question_detail:token"})["resources"]
    patch = {"question_detail": {"enabled": True, "config": {"api_url": "http://new.example"}}}

    applied = apply_resource_secret_fields(
        vault, workspace["id"], patch, current, settings.resource_providers.schemas
    )

    assert applied["question_detail"]["config"]["token"] == {
        "secret_ref": "resource:question_detail:token"
    }


def test_apply_resource_secret_fields_masked_echo_keeps_stored(vault, job_db, settings):
    workspace = job_db.create_workspace("vault-echo")
    current = _resource_config({"secret_ref": "resource:question_detail:token"})["resources"]
    patch = {"question_detail": {"enabled": True, "config": {"token": {"secret_set": True}}}}

    applied = apply_resource_secret_fields(
        vault, workspace["id"], patch, current, settings.resource_providers.schemas
    )

    assert applied["question_detail"]["config"]["token"] == {
        "secret_ref": "resource:question_detail:token"
    }


def test_strip_resource_secret_fields_removes_secret_values(settings):
    resources = _resource_config(PLAINTEXT)["resources"]
    stripped = strip_resource_secret_fields(resources, settings.resource_providers.schemas)
    assert "token" not in stripped["question_detail"]["config"]
    assert stripped["question_detail"]["config"]["api_url"] == (
        "http://cms.example.com/question/detail"
    )


def test_effective_cms_config_resolves_ref(vault, job_db, settings):
    workspace = job_db.create_workspace("vault-effective")
    vault.set(workspace["id"], "resource:question_detail:token", PLAINTEXT)
    job_db.update_workspace(
        workspace["id"],
        resource_config=_resource_config({"secret_ref": "resource:question_detail:token"}),
    )
    context = {"settings_config": settings.config, "job_db": job_db}

    resolved = _effective_cms_config({"workspace_id": workspace["id"], "batch_id": ""}, context)

    assert resolved["token"] == PLAINTEXT


def test_effective_cms_config_passes_legacy_plaintext_through(job_db, settings, vault_key):
    workspace = job_db.create_workspace("vault-legacy")
    job_db.update_workspace(workspace["id"], resource_config=_resource_config("legacy-plain-text"))
    context = {"settings_config": settings.config, "job_db": job_db}

    resolved = _effective_cms_config({"workspace_id": workspace["id"], "batch_id": ""}, context)

    assert resolved["token"] == "legacy-plain-text"


def test_effective_cms_config_ref_without_key_raises(job_db, settings, monkeypatch):
    workspace = job_db.create_workspace("vault-no-key-resolve")
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    VaultService(job_db.path, {}).set(workspace["id"], "resource:question_detail:token", PLAINTEXT)
    job_db.update_workspace(
        workspace["id"],
        resource_config=_resource_config({"secret_ref": "resource:question_detail:token"}),
    )
    # Key removed after the secret was written: resolution must fail loudly.
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)
    context = {"settings_config": {}, "job_db": job_db}

    with pytest.raises(VaultMasterKeyMissingError):
        _effective_cms_config({"workspace_id": workspace["id"], "batch_id": ""}, context)


def test_intake_freeze_stores_secret_ref_not_plaintext(vault, job_db, settings):
    workspace = job_db.create_workspace(
        "vault-freeze", default_workflow_key="question_comprehension_info"
    )
    definition = WorkflowCatalogService(settings).definition("question_comprehension_info")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    vault.set(workspace["id"], "resource:question_detail:token", PLAINTEXT)
    job_db.update_workspace(
        workspace["id"],
        resource_config=_resource_config({"secret_ref": "resource:question_detail:token"}),
    )
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    result = service.create_batch(
        workspace["id"],
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
        },
    )

    batch = job_db.get_batch(str(result["batch"]["id"]))
    payload_text = str(batch["source_payload_json"])
    payload = json.loads(payload_text)
    frozen = payload["resource_config"]["resources"]["question_detail"]["config"]
    assert frozen["token"] == {"secret_ref": "resource:question_detail:token"}
    assert PLAINTEXT not in payload_text
