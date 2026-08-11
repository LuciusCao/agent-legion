"""Vault service: encryption round-trip, key handling, secret_ref resolution."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import AgentService, published_agent_definitions
from server.app.services.executor_definition_service import hydrate_executor_definitions
from server.app.services.job_intake import JobIntakeService
from server.app.services.node_secrets import node_secret_name
from server.app.services.vault import (
    VaultError,
    VaultMasterKeyMissingError,
    VaultService,
)
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_node_config import update_workspace_node_config

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
            "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
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
    name = node_secret_name("question_comprehension_info", "fetch_questions", "token")
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


def test_resolve_secret_refs_without_master_key_raises(job_db, monkeypatch):
    # Key removed after the secret was written: resolution must fail loudly.
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    workspace = job_db.create_workspace("vault-no-key-resolve")
    VaultService(job_db.path, {}).set(workspace["id"], "api-token", PLAINTEXT)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)

    with pytest.raises(VaultMasterKeyMissingError):
        VaultService(job_db.path, {}).resolve_secret_refs(
            {"token": {"secret_ref": "api-token"}}, workspace["id"]
        )


def test_intake_freeze_stores_secret_ref_not_plaintext(vault, job_db, settings):
    # The bare settings fixture does not hydrate executor definitions
    # (create_app does); the node config schema chain needs the seeded catalog.
    hydrate_executor_definitions(settings)
    # fetch_questions no longer declares secret fields (CMS credentials moved
    # to instance-level external connections); republish the generate_key_info
    # agent with a secret field so the intake freeze chain still has a
    # schema-declared node secret to divert into the vault.
    agent_service = AgentService(settings.database_url)
    agent_service.save_draft(
        "question-key-info-v1",
        AgentDefinition(
            capability="generate_key_info",
            runtime="velites",
            skill="question_comprehension_info/generate_key_info",
            config_schema={
                "type": "object",
                "properties": {
                    "api_url": {"type": "string"},
                    "token": {"type": "string", "secret": True},
                },
            },
        ),
        created_by="test-seed",
    )
    agent_service.publish("question-key-info-v1")
    workspace = job_db.create_workspace(
        "vault-freeze", default_workflow_key="question_comprehension_info"
    )
    definition = WorkflowCatalogService(settings).definition("question_comprehension_info")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    update_workspace_node_config(
        job_db,
        WorkflowCatalogService(settings),
        published_agent_definitions(settings.database_url),
        job_db.get_workspace(workspace["id"]),
        {
            "nodeConfig": {
                "generate_key_info": {
                    "token": PLAINTEXT,
                    "api_url": "http://cms.example.com/question/detail",
                }
            }
        },
        settings.executor_definitions,
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
    frozen = payload["node_config"]["generate_key_info"]
    name = node_secret_name("question_comprehension_info", "generate_key_info", "token")
    assert frozen["token"] == {"secret_ref": name}
    assert frozen["api_url"] == "http://cms.example.com/question/detail"
    assert PLAINTEXT not in payload_text
    assert vault.get(workspace["id"], name) == PLAINTEXT
